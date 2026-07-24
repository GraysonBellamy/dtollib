# TEDS — Transducer Electronic Data Sheets

IEEE-1451.4 smart transducers store their calibration metadata either on
the sensor itself (**hardware TEDS**) or in a sidecar file (**virtual
TEDS**). `dtollib` exposes four readers backed by the SDK's
`olDaRead{StrainGage,BridgeSensor}{Hardware,Virtual}Teds`.

> **Hardware TEDS requires an intelligent multi-sensor module**
> (DT9828 / DT9829 / DT9837). The owned DT9805 / DT9806 report
> `supports_multisensor = False`, so the hardware readers raise
> `DtolCapabilityError` *before* the SDK is called. Virtual-TEDS reads
> parse a file and need no hardware — they work on any board. Real-sensor
> verification is deferred until a multi-sensor module is on the bench;
> the read path is exercised against `FakeDtolBackend` today.

## Reading TEDS

```python
from dtollib import (
    read_strain_gage_teds,
    read_strain_gage_virtual_teds,
    read_bridge_sensor_teds,
    read_bridge_sensor_virtual_teds,
)

# Hardware TEDS — capability-gated (raises DtolCapabilityError on DT9805/06):
teds = read_strain_gage_teds(backend, hdass, channel=0)
print(teds.gage_factor, teds.serial_number, teds.version_letter)

# Virtual TEDS — reads a file, no hardware needed:
teds = read_strain_gage_virtual_teds(backend, "gage_42.teds")

# Bridge sensors:
bridge = read_bridge_sensor_teds(backend, hdass, channel=1)
print(bridge.excitation_nominal_v, bridge.max_physical_value)
```

## Returned data

`StrainGageTeds` and `BridgeSensorTeds` are frozen dataclasses exposing
the common identity + calibration fields (manufacturer/model/serial,
gage factor, resistances, excitation, physical range). Every field the
SDK populated is also available under `.raw` (a plain dict keyed by the
C struct field names from `TedsApi.h`), so less-common members are
reachable without a dedicated attribute.

```python
teds = read_strain_gage_teds(backend, hdass, 0)
teds.gage_factor  # typed accessor
teds.raw["gageTransSens"]  # any other struct field
```

## Volts → engineering conversion

Once you have a bridge output voltage (from a normal `poll` / `record`),
turn it into engineering units with the SDK conversions:

```python
from dtollib import strain_from_volts, bridge_value_from_volts

# Gage/bridge parameters are read off the channel spec; you supply the
# measured unstrained + strained voltages.
epsilon = strain_from_volts(backend, strain_spec, v_unstrained=0.0, v_strained=0.0025)
value = bridge_value_from_volts(backend, bridge_spec, v_unstrained=0.0, v_strained=0.01)
```

For purely application-side rosette math (no SDK round-trip) see
`compute_rectangular_rosette` / `compute_delta_rosette` in
[`dtollib.utils`](api/capi.md).
