# Decimal Amps Test — 2026-02-24

## Setup
- OCPP Server v0.9.27: `chargingRateUnit=A` with decimal float limit
- Wallbox: AcTec EV-AC22K, 3-phase
- Conversion: `limit_a = round(watts / (3 × 230), 1)`

## Test Results

| Demand (W) | Sent (A) | Wallbox (W) | Car (kW) | Effective A | OCPP Response |
|-----------|---------|-------------|----------|------------|---------------|
| 5000 | 7.2 | 4390 | ~4.6 | 7 (floored) | Accepted |
| 5500 | 8.0 | 5140 | 5.37 | 8 (exact) | Accepted |
| 5865 | 8.5 | 5143 | 5.37 | 8 (floored) | Accepted |

## Raw OCPP Messages

### Test 1: 5000W → 7.2A

**Server sends SetChargingProfile (07:15:04):**
```
2026-02-24 07:15:04 [INFO] ocpp: Actec: send [2,"d13dc558-749e-48b5-ae8d-d082b7d203d5","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":1,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":7.2,"numberPhases":3}]}}}]
```

**Wallbox replies Accepted:**
```
2026-02-24 07:15:04 [INFO] ocpp: Actec: receive message [3,"d13dc558-749e-48b5-ae8d-d082b7d203d5",{"status":"Accepted"}]
```

**Wallbox MeterValues (07:15–07:21) — delivers 7A, not 7.2A:**
```
2026-02-24 07:15:20 [INFO] ocpp: Actec: receive message [2,"1063","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:15:22.000Z","sampledValue":[{"value":"60","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.56","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.29","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.51","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1487","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1429","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1481","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.3","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.2","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:16:19 [INFO] ocpp: Actec: receive message [2,"1065","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:16:22.000Z","sampledValue":[{"value":"140","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.48","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.22","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.40","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1474","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1418","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1474","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.6","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.4","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:17:20 [INFO] ocpp: Actec: receive message [2,"1067","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:17:22.000Z","sampledValue":[{"value":"210","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.55","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.29","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.50","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1486","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1432","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1481","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.5","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:18:20 [INFO] ocpp: Actec: receive message [2,"1069","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:18:22.000Z","sampledValue":[{"value":"280","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.24","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"5.99","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.21","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1404","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1341","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1391","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"231.0","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.5","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:19:20 [INFO] ocpp: Actec: receive message [2,"1071","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:19:22.000Z","sampledValue":[{"value":"350","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.58","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.32","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.50","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1489","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1433","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1478","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.3","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:20:20 [INFO] ocpp: Actec: receive message [2,"1073","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:20:22.000Z","sampledValue":[{"value":"430","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.48","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.23","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.40","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1480","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1432","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1478","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.5","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:21:20 [INFO] ocpp: Actec: receive message [2,"1075","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:21:22.000Z","sampledValue":[{"value":"500","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"6.58","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"6.31","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"6.50","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1481","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1427","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1465","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.7","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.1","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]
```

| Time | L1 (A) | L2 (A) | L3 (A) | L1 (W) | L2 (W) | L3 (W) | Total (W) | V L1 | V L2 |
|------|--------|--------|--------|--------|--------|--------|-----------|------|------|
| 07:15:20 | **6.56** | **6.29** | **6.51** | 1487 | 1429 | 1481 | 4397 | 230.3 | 231.2 |
| 07:16:19 | **6.48** | **6.22** | **6.40** | 1474 | 1418 | 1474 | 4366 | 230.6 | 231.4 |
| 07:17:20 | **6.55** | **6.29** | **6.50** | 1486 | 1432 | 1481 | 4399 | 230.8 | 231.5 |
| 07:18:20 | **6.24** | **5.99** | **6.21** | 1404 | 1341 | 1391 | 4136 | 231.0 | 231.5 |
| 07:19:20 | **6.58** | **6.32** | **6.50** | 1489 | 1433 | 1478 | 4400 | 230.8 | 231.3 |
| 07:20:20 | **6.48** | **6.23** | **6.40** | 1480 | 1432 | 1478 | 4390 | 230.8 | 231.5 |
| 07:21:20 | **6.58** | **6.31** | **6.50** | 1481 | 1427 | 1465 | 4373 | 230.7 | 231.1 |

**Average: ~6.4A per phase, ~4380W total → confirms 7A internal (7.2A floored)**

---

### Test 2: 5500W → 8.0A

**Server sends SetChargingProfile (07:21:25):**
```
2026-02-24 07:21:25 [INFO] ocpp: Actec: send [2,"482b3e62-7928-461d-9e5e-1f29446f91da","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":1,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":8.0,"numberPhases":3}]}}}]
```

**Wallbox replies Accepted:**
```
2026-02-24 07:21:25 [INFO] ocpp: Actec: receive message [3,"482b3e62-7928-461d-9e5e-1f29446f91da",{"status":"Accepted"}]
```

**Wallbox MeterValues (07:22–07:26) — delivers 8A:**
```
2026-02-24 07:22:20 [INFO] ocpp: Actec: receive message [2,"1077","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:22:22.000Z","sampledValue":[{"value":"580","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.60","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.36","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.50","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1734","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1681","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1722","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.4","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:23:20 [INFO] ocpp: Actec: receive message [2,"1079","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:23:22.000Z","sampledValue":[{"value":"670","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.59","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.37","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.51","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1736","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1683","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1721","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"231.0","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.3","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:24:20 [INFO] ocpp: Actec: receive message [2,"1081","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:24:22.000Z","sampledValue":[{"value":"760","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.61","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.38","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.52","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1735","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1684","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1722","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.6","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.0","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:25:20 [INFO] ocpp: Actec: receive message [2,"1083","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:25:22.000Z","sampledValue":[{"value":"840","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.60","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.39","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.53","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1715","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1664","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1704","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.2","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:26:20 [INFO] ocpp: Actec: receive message [2,"1085","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:26:23.000Z","sampledValue":[{"value":"930","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.60","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.38","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.52","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1736","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1685","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1724","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.9","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.3","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]
```

| Time | L1 (A) | L2 (A) | L3 (A) | L1 (W) | L2 (W) | L3 (W) | Total (W) | V L1 | V L2 |
|------|--------|--------|--------|--------|--------|--------|-----------|------|------|
| 07:22:20 | **7.60** | **7.36** | **7.50** | 1734 | 1681 | 1722 | 5137 | 230.8 | 231.4 |
| 07:23:20 | **7.59** | **7.37** | **7.51** | 1736 | 1683 | 1721 | 5140 | 231.0 | 231.3 |
| 07:24:20 | **7.61** | **7.38** | **7.52** | 1735 | 1684 | 1722 | 5141 | 230.6 | 231.0 |
| 07:25:20 | **7.60** | **7.39** | **7.53** | 1715 | 1664 | 1704 | 5083 | 230.8 | 231.2 |
| 07:26:20 | **7.60** | **7.38** | **7.52** | 1736 | 1685 | 1724 | 5145 | 230.9 | 231.3 |

**Average: ~7.5A per phase, ~5129W total → confirms 8A internal**

---

### Test 3: 5865W → 8.5A

**Server sends SetChargingProfile (07:26:24):**
```
2026-02-24 07:26:24 [INFO] ocpp: Actec: send [2,"3842527c-ab25-4516-a5fa-12f347280097","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":1,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":8.5,"numberPhases":3}]}}}]
```

**Wallbox replies Accepted:**
```
2026-02-24 07:26:25 [INFO] ocpp: Actec: receive message [3,"3842527c-ab25-4516-a5fa-12f347280097",{"status":"Accepted"}]
```

**Wallbox MeterValues (07:27–07:31) — delivers 8A, not 8.5A (floored):**
```
2026-02-24 07:27:20 [INFO] ocpp: Actec: receive message [2,"1087","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:27:23.000Z","sampledValue":[{"value":"1010","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.59","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.38","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.52","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1725","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1672","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1721","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.2","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:28:20 [INFO] ocpp: Actec: receive message [2,"1089","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:28:23.000Z","sampledValue":[{"value":"1100","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.60","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.40","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.52","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1734","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1686","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1723","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.7","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"231.1","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:29:20 [INFO] ocpp: Actec: receive message [2,"1091","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:29:23.000Z","sampledValue":[{"value":"1180","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.59","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.40","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.52","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1719","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1674","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1708","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.4","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"230.5","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:30:20 [INFO] ocpp: Actec: receive message [2,"1094","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:30:23.000Z","sampledValue":[{"value":"1270","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.61","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.37","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.53","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1728","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1666","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1707","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.4","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"230.6","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]

2026-02-24 07:31:20 [INFO] ocpp: Actec: receive message [2,"1096","MeterValues",{"connectorId":1,"transactionId":1,"meterValue":[{"timestamp":"2026-02-24T06:31:23.000Z","sampledValue":[{"value":"1360","context":"Sample.Periodic","format":"Raw","measurand":"Energy.Active.Import.Register","unit":"Wh"},{"value":"7.56","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L1","unit":"A"},{"value":"7.37","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L2","unit":"A"},{"value":"7.49","context":"Sample.Periodic","format":"Raw","measurand":"Current.Import","phase":"L3","unit":"A"},{"value":"1723","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L1","unit":"W"},{"value":"1677","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L2","unit":"W"},{"value":"1717","context":"Sample.Periodic","format":"Raw","measurand":"Power.Active.Import","phase":"L3","unit":"W"},{"value":"0","context":"Sample.Periodic","format":"Raw","measurand":"SoC","unit":"Percent"},{"value":"230.5","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L1","unit":"V"},{"value":"230.8","context":"Sample.Periodic","format":"Raw","measurand":"Voltage","phase":"L2","unit":"V"}]}]}]
```

| Time | L1 (A) | L2 (A) | L3 (A) | L1 (W) | L2 (W) | L3 (W) | Total (W) | V L1 | V L2 |
|------|--------|--------|--------|--------|--------|--------|-----------|------|------|
| 07:27:20 | **7.59** | **7.38** | **7.52** | 1725 | 1672 | 1721 | 5118 | 230.8 | 231.2 |
| 07:28:20 | **7.60** | **7.40** | **7.52** | 1734 | 1686 | 1723 | 5143 | 230.7 | 231.1 |
| 07:29:20 | **7.59** | **7.40** | **7.52** | 1719 | 1674 | 1708 | 5101 | 230.4 | 230.5 |
| 07:30:20 | **7.61** | **7.37** | **7.53** | 1728 | 1666 | 1707 | 5101 | 230.4 | 230.6 |
| 07:31:20 | **7.56** | **7.37** | **7.49** | 1723 | 1677 | 1717 | 5117 | 230.5 | 230.8 |

**Average: ~7.5A per phase, ~5116W total → identical to 8.0A test, confirms 8.5A floored to 8A**

## Conclusion

The wallbox **accepts** decimal amp values (OCPP response: Accepted) but **floors them to the nearest integer** internally:
- 7.2A → 7A output (~4380W)
- 8.0A → 8A output (~5130W)
- 8.5A → 8A output (~5116W, identical to 8.0A)

MeterValues confirm the flooring: the 8.0A and 8.5A tests produced identical per-phase currents (~7.5A metered) and power (~5120W total). Decimal amps provide no finer granularity than integer amps.
