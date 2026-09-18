"""Pond physics.

This replaces the magic-number model. Every term below is a named physical
process with a defensible form, and every rate is normalised by pond depth so
a 0.8 m nursery and a 3 m grow-out pond genuinely behave differently.

State per timestep:
    T   bulk water temperature  (degC)
    C   volume-averaged dissolved oxygen (mg/L)

The depth profile is applied as a mass-conserving diagnostic on top of C:
stratification redistributes oxygen through the column, it does not create or
destroy it. That keeps the 3D view honest.

The engine is deliberately behind an interface (`ForecastEngine`) so a learned
model can be dropped in later, either as a replacement or - better - as a
residual correction on top of this physics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import settings

# --------------------------------------------------------------- constants

RHO_CP = 4.18e6        # J / m3 / K, volumetric heat capacity of water
STEFAN = 5.67e-8       # W / m2 / K4
ALBEDO = 0.06          # water surface shortwave albedo

SPECIES = {
    "tilapia":      {"label": "Tilapia",           "critical": 3.0, "warning": 4.5, "lethal": 1.5},
    "vannamei":     {"label": "L. vannamei shrimp", "critical": 3.5, "warning": 5.0, "lethal": 2.0},
    "carp":         {"label": "Indian major carp",  "critical": 3.0, "warning": 4.0, "lethal": 1.3},
    "pangasius":    {"label": "Pangasius",          "critical": 2.0, "warning": 3.5, "lethal": 1.0},
    "seabass":      {"label": "Asian seabass",      "critical": 4.0, "warning": 5.5, "lethal": 2.5},
}


def species_thresholds(key: str) -> dict:
    return SPECIES.get(key, SPECIES["tilapia"])


# --------------------------------------------------------------- utilities

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def do_saturation(temp_c: float, salinity_ppt: float = 0.0, altitude_m: float = 0.0) -> float:
    """Benson & Krause equilibrium oxygen concentration, mg/L."""
    t = temp_c + 273.15
    ln_c = (
        -139.34411
        + 1.575701e5 / t
        - 6.642308e7 / t**2
        + 1.243800e10 / t**3
        - 8.621949e11 / t**4
    )
    c = math.exp(ln_c)
    if salinity_ppt > 0:
        ln_s = -salinity_ppt * (0.017674 - 10.754 / t + 2140.7 / t**2)
        c *= math.exp(ln_s)
    if altitude_m > 0:
        c *= math.exp(-altitude_m / 8400.0)
    return c


def saturation_vapour_pressure(temp_c: float) -> float:
    """Magnus formula, millibar."""
    return 6.11 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def light_extinction(turbidity_ntu: float) -> float:
    """Beer-Lambert attenuation coefficient, 1/m, from turbidity."""
    return 0.25 + 0.055 * max(0.0, turbidity_ntu)


def mean_column_light(surface_w_m2: float, k: float, depth_m: float) -> float:
    """Depth-averaged irradiance. This is why a turbid deep pond produces far
    less oxygen than a clear shallow one at the same surface radiation."""
    if depth_m <= 0 or k <= 0:
        return surface_w_m2
    return surface_w_m2 * (1.0 - math.exp(-k * depth_m)) / (k * depth_m)


# ------------------------------------------------------------------ inputs

@dataclass
class PondConfig:
    length_m: float = 40.0
    width_m: float = 25.0
    depth_m: float = 2.0
    species: str = "tilapia"
    stock_count: int = 2000
    avg_weight_g: float = 120.0
    aerator_count: int = 1
    aerator_kw: float = 1.5
    power_cost: float = 7.5          # currency per kWh
    salinity_ppt: float = 0.0

    @property
    def area_m2(self) -> float:
        return self.length_m * self.width_m

    @property
    def volume_m3(self) -> float:
        return self.area_m2 * self.depth_m

    @property
    def biomass_kg(self) -> float:
        return self.stock_count * self.avg_weight_g / 1000.0

    @property
    def density_kg_m3(self) -> float:
        v = self.volume_m3
        return self.biomass_kg / v if v > 0 else 0.0


@dataclass
class WaterState:
    dissolved_oxygen: float = 6.0
    water_temperature: float = 29.0
    ph: float = 7.6
    turbidity: float = 22.0
    alkalinity: float = 90.0         # mg/L as CaCO3, buffers pH swing


@dataclass
class HourWeather:
    time: str
    air_temperature: float
    wind_speed_ms: float
    solar_radiation: float
    relative_humidity: float = 75.0
    cloud_cover: float = 40.0


@dataclass
class EngineOptions:
    aerator_schedule: list[bool] = field(default_factory=list)
    feed_kg_per_day: float = 0.0


# ----------------------------------------------------------- process terms

def photosynthesis(light_w_m2: float, chl_proxy: float, temp_c: float) -> float:
    """Gross photosynthetic oxygen production, mg/L/h.

    Saturating light response (Steele-like) rather than the linear term used
    before, scaled by an algal biomass proxy derived from turbidity.
    """
    ik = 120.0                      # saturation irradiance, W/m2
    p_max = 1.55 * chl_proxy
    theta = 1.05 ** (temp_c - 25.0)
    return p_max * (light_w_m2 / (light_w_m2 + ik)) * theta


def respiration(temp_c: float, density_kg_m3: float, chl_proxy: float) -> float:
    """Combined plankton and fish respiration, mg/L/h."""
    base = 0.14 * chl_proxy                       # planktonic
    fish = 0.32 * density_kg_m3                   # stock demand
    return (base + fish) * 1.07 ** (temp_c - 20.0)


def sediment_demand(temp_c: float, depth_m: float, feed_load: float) -> float:
    """Sediment oxygen demand expressed per litre of water column, mg/L/h.

    SOD is an areal flux, so dividing by depth is what makes it dominate in
    shallow ponds and matter less in deep ones.
    """
    areal = (0.55 + 0.45 * feed_load) * 1.065 ** (temp_c - 20.0)   # g O2 / m2 / h
    return areal / max(0.3, depth_m)


def wind_reaeration(wind_ms: float, depth_m: float) -> float:
    """Surface exchange coefficient k2, 1/h. Drives DO toward saturation and,
    critically, stops driving once saturation is reached."""
    return (0.11 + 0.042 * wind_ms ** 1.7) / max(0.3, depth_m)


def aerator_transfer(cfg: PondConfig, temp_c: float, do_now: float, sat: float) -> float:
    """Actual oxygen transfer rate of the running aerators, mg/L/h.

    Standard transfer rating corrected to field conditions by the classic
    alpha / beta / theta factors. Transfer collapses as water approaches
    saturation, which is the behaviour the old flat +0.55 term got wrong.
    """
    alpha, beta, theta = 0.85, 0.98, 1.024
    sotr = cfg.aerator_count * cfg.aerator_kw * 1.5        # kg O2 / h at SAE 1.5
    driving = (beta * sat - do_now) / 9.09
    if driving <= 0:
        return 0.0
    otr_kg_h = sotr * alpha * driving * theta ** (temp_c - 20.0)
    return otr_kg_h * 1000.0 / max(1.0, cfg.volume_m3)


def surface_energy_balance(temp_w: float, w: HourWeather) -> float:
    """Net heat flux into the water surface, W/m2.

    Shortwave in, longwave out, sensible exchange, and latent heat of
    evaporation - the term the old model omitted entirely, and the reason
    ponds cool overnight even when air temperature is high.
    """
    sw = w.solar_radiation * (1.0 - ALBEDO)

    emis_air = 0.75 + 0.0025 * w.cloud_cover
    lw_out = 0.97 * STEFAN * (temp_w + 273.15) ** 4
    lw_in = emis_air * STEFAN * (w.air_temperature + 273.15) ** 4

    sensible = (6.9 + 3.9 * w.wind_speed_ms) * (temp_w - w.air_temperature)

    es_w = saturation_vapour_pressure(temp_w)
    es_a = saturation_vapour_pressure(w.air_temperature) * (w.relative_humidity / 100.0)
    latent = (2.7 + 3.1 * w.wind_speed_ms) * max(0.0, es_w - es_a)

    return sw + lw_in - lw_out - sensible - latent


# ------------------------------------------------------------ stratification

def stratification_strength(
    solar_norm: float, wind_ms: float, depth_m: float,
    turbidity: float, aerator_on: bool,
) -> float:
    """How strongly the column separates, in mg/L between surface and bed.

    Builds with solar heating and depth, destroyed by wind mixing and by a
    running aerator. Turbid water heats shallower, so it stratifies harder
    near the top but shades the bottom.
    """
    s = (0.35 + 1.9 * solar_norm) * (depth_m / 2.0)
    s *= math.exp(-wind_ms / 2.6)
    s *= 0.85 + 0.35 * min(1.0, turbidity / 45.0)
    if aerator_on:
        s *= 0.18
    return max(0.0, s)


def build_profile(mean_do: float, strength: float, depth_m: float,
                  n_layers: int | None = None) -> list[dict]:
    """Distribute the volume-averaged DO through the column.

    The shape is normalised to zero mean, so the volume average of the layers
    equals `mean_do` exactly. Stratification moves oxygen around; it never
    invents it.
    """
    n = n_layers or settings.layers
    shape = []
    for i in range(n):
        f = (i + 0.5) / n
        thermocline = 1.0 / (1.0 + math.exp(-(f - 0.42) * 7.0))
        shape.append(0.5 - thermocline)
    offset = sum(shape) / n
    shape = [s - offset for s in shape]

    layers = []
    for i, s in enumerate(shape):
        f = (i + 0.5) / n
        value = mean_do + strength * s * 2.0
        layers.append({
            "index": i,
            "depth_m": round(f * depth_m, 2),
            "dissolved_oxygen": round(max(0.05, value), 2),
        })
    return layers


# ------------------------------------------------------------------ engine

class PhysicsEngine:
    """Deterministic forward model. Integrates below the hour to stay stable."""

    name = "physics-v2"

    def __init__(self, substeps: int | None = None):
        self.substeps = substeps or settings.substeps_per_hour

    def run(
        self,
        cfg: PondConfig,
        initial: WaterState,
        weather: list[HourWeather],
        options: EngineOptions | None = None,
    ) -> dict:
        options = options or EngineOptions()
        horizon = len(weather)
        dt = 1.0 / self.substeps

        temp = initial.water_temperature
        do = initial.dissolved_oxygen
        ph = initial.ph
        turbidity = initial.turbidity

        chl_proxy = clamp(0.45 + turbidity / 38.0, 0.4, 2.1)
        k_ext = light_extinction(turbidity)
        feed_load = clamp(options.feed_kg_per_day / max(1.0, cfg.area_m2 / 100.0), 0.0, 3.0)
        thresholds = species_thresholds(cfg.species)

        results = []
        energy_kwh = 0.0
        aerator_hours = 0

        for h, w in enumerate(weather):
            on = bool(options.aerator_schedule[h]) if h < len(options.aerator_schedule) else False
            if on:
                aerator_hours += 1
                energy_kwh += cfg.aerator_count * cfg.aerator_kw

            for _ in range(self.substeps):
                sat = do_saturation(temp, cfg.salinity_ppt)

                # --- heat ---
                flux = surface_energy_balance(temp, w)
                temp += flux * 3600.0 * dt / (RHO_CP * max(0.3, cfg.depth_m))
                temp = clamp(temp, 12.0, 42.0)

                # --- oxygen ---
                light = mean_column_light(w.solar_radiation, k_ext, cfg.depth_m)
                gain_photo = photosynthesis(light, chl_proxy, temp)
                loss_resp = respiration(temp, cfg.density_kg_m3, chl_proxy)
                loss_sod = sediment_demand(temp, cfg.depth_m, feed_load)
                exchange = wind_reaeration(w.wind_speed_ms, cfg.depth_m) * (sat - do)
                gain_aer = aerator_transfer(cfg, temp, do, sat) if on else 0.0

                do += (gain_photo - loss_resp - loss_sod + exchange + gain_aer) * dt
                do = clamp(do, 0.05, sat * 1.6)

                # --- pH, buffered by alkalinity ---
                net_production = gain_photo - loss_resp - loss_sod
                buffer = clamp(initial.alkalinity / 90.0, 0.4, 3.0)
                ph += (net_production * 0.085 / buffer) * dt
                ph = clamp(ph, 6.0, 9.6)

            solar_norm = clamp(w.solar_radiation / 850.0, 0.0, 1.0)
            strength = stratification_strength(
                solar_norm, w.wind_speed_ms, cfg.depth_m, turbidity, on
            )
            layers = build_profile(do, strength, cfg.depth_m)
            surface_do = layers[0]["dissolved_oxygen"]
            bottom_do = layers[-1]["dissolved_oxygen"]
            min_do = min(l["dissolved_oxygen"] for l in layers)

            if min_do < thresholds["lethal"]:
                risk = "lethal"
            elif min_do < thresholds["critical"]:
                risk = "critical"
            elif min_do < thresholds["warning"]:
                risk = "warning"
            else:
                risk = "safe"

            results.append({
                "hour": h + 1,
                "time": w.time,
                "mean_do": round(do, 2),
                "surface_do": surface_do,
                "bottom_do": bottom_do,
                "min_do": round(min_do, 2),
                "saturation": round(do_saturation(temp, cfg.salinity_ppt), 2),
                "water_temperature": round(temp, 2),
                "ph": round(ph, 2),
                "turbidity": round(turbidity, 1),
                "air_temperature": round(w.air_temperature, 1),
                "wind_speed_ms": round(w.wind_speed_ms, 2),
                "solar_radiation": round(w.solar_radiation, 1),
                "stratification": round(strength, 2),
                "aerator_on": on,
                "risk": risk,
                "layers": layers,
            })

        first_risk = next(
            (r["hour"] for r in results if r["risk"] in ("critical", "lethal")), None
        )
        hours_at_risk = sum(1 for r in results if r["risk"] in ("critical", "lethal"))

        return {
            "engine": self.name,
            "horizon_hours": horizon,
            "species": thresholds["label"],
            "thresholds": thresholds,
            "first_risk_hour": first_risk,
            "hours_at_risk": hours_at_risk,
            "lowest_do": round(min(r["min_do"] for r in results), 2) if results else None,
            "aerator_hours": aerator_hours,
            "energy_kwh": round(energy_kwh, 2),
            "energy_cost": round(energy_kwh * cfg.power_cost, 2),
            "results": results,
        }


def recommend_schedule(
    cfg: PondConfig,
    initial: WaterState,
    weather: list[HourWeather],
    options: EngineOptions | None = None,
) -> dict:
    """Cheapest aeration schedule that keeps the whole column safe.

    Greedy: run the baseline, then switch on the hour before each violation
    and re-run, until the horizon is clear or every hour is already on. This is
    the part that turns a forecast into an instruction a farmer can act on.
    """
    engine = PhysicsEngine()
    base_opts = options or EngineOptions()
    horizon = len(weather)
    schedule = [False] * horizon
    thresholds = species_thresholds(cfg.species)

    run = engine.run(cfg, initial, weather, EngineOptions(
        aerator_schedule=schedule, feed_kg_per_day=base_opts.feed_kg_per_day
    ))

    for _ in range(horizon):
        bad = next(
            (r["hour"] for r in run["results"]
             if r["min_do"] < thresholds["warning"]),
            None,
        )
        if bad is None:
            break
        start = max(0, bad - 2)
        changed = False
        for i in range(start, min(horizon, bad + 1)):
            if not schedule[i]:
                schedule[i] = True
                changed = True
        if not changed:
            break
        run = engine.run(cfg, initial, weather, EngineOptions(
            aerator_schedule=schedule, feed_kg_per_day=base_opts.feed_kg_per_day
        ))

    run["recommended_schedule"] = schedule
    return run
