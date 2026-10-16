# Boost — вътрешен контекст за разработка

> Този файл е работен контекст за бъдеща работа по Boost. Поддържай го при всяка промяна на логиката, конфигурацията, тестовете или интеграцията. Не е потребителска документация.

## 1. Какво представлява Boost

Boost е функция за бързо възстановяване на температурата след проветряване. Тя използва вече избрания `Cooler` entity (`cooler_entity_id`), без отделен entity picker.

- При отваряне на **прозорец** се запомня стайната температура.
- При затваряне се сравнява запомнената температура с текущата.
- Голям спад активира heat boost.
- Голямо покачване активира cool boost.
- И в двата случая целта е нормалната `bt_target_temp` на Better Thermostat.
- Door transitions не участват в Boost.
- Boost не е отделен двоен setpoint режим и не създава собствена термична модель.

Основната идея е бързото вторично устройство (fan-coil, heat pump, air conditioner или подобно) да възстанови стаята, докато основното управление и съществуващата телеметрия продължават да работят нормално.

## 2. Какво сме направили до момента

Историята на функцията в git е концентрирана основно в следните промени:

1. `e8bb3ebb` — добавена е базовата симетрична heat/cool логика през Cooler entity, прозоречният trigger и маршрутизацията в control queue.
2. `17d3d439` — добавени са основните unit тестове за tracker, window reopen, config flow и control.
3. `95349b7a` — slow trend/EMA guard-ът започва да се обновява във всеки control cycle, а не само по време на активен Boost.
4. `5a5d05ed` — добавени са solar awareness, cooldown, отделен cooling threshold, тестова услуга, диагностичен sensor и свързаните преводи/настройки.
5. `29f9c86a` и `21c21818` — документация в README/configuration/deep explanation и преводи във всички locale catalogues.
6. `1a650216` — поправено е закъсняващото състояние на Boost Status sensor.
7. `78dad0dc` — при край Boost възстановява точно предишните HVAC mode, temperature и fan mode, вместо да прави нова приблизителна cooler decision.
8. `0cac82ac` и `685b4303` — коригирани са Fahrenheit/setpoint и Celsius read paths за TS0601/SEA801 quirks.

Текущият код е в `main`; Boost историята е наследена и от `feature/boost-heater`/`origin/feature/boost-heater`.

## 3. Къде е реализацията

### Основен модул

`custom_components/better_thermostat/utils/boost_heater.py`

Това е центърът на функцията. Важни части:

- `BoostHeaterTracker` — lifecycle state, запомнена температура при open, посока, snapshot за restore, start/end timestamps, slow EMA и last action.
- `build_boost_heater_user_fields()` — динамични config-flow полета; fan modes се показват само ако Cooler ги отчита.
- `normalize_boost_heater_user_submission()` — clamp/reset на всички настройки и премахване на stale values при изключване.
- `record_window_transition()` — приема само window transition, записва open temperature или оценява close.
- `_boost_should_stop()` — target/tolerance, anticipatory lag projection, slow trend guard, solar gain и safety timeout.
- `update_boost_trend()` — обновява EMA и публикува status във всеки control cycle.
- `control_boost()` — изпълнява командите към Cooler и при нужда възстановява snapshot-а.
- `trigger_test_boost()` — ръчно вътрешно тестване през entity service.

### Интеграционни точки

- `custom_components/better_thermostat/events/contact.py` — извиква `record_window_transition()` на debounce commit; caller-ът филтрира ролята до `window`.
- `custom_components/better_thermostat/utils/controlling.py` — извиква `update_boost_trend()` всеки cycle; ако има активен Boost, извиква `control_boost()` вместо `control_cooler()`.
- `custom_components/better_thermostat/climate.py` — съхранява Boost настройки, създава tracker-а и регистрира `better_thermostat.test_boost`.
- `custom_components/better_thermostat/sensor.py` — създава `BetterThermostatBoostStatusSensor`.
- `custom_components/better_thermostat/services.yaml` — описва `test_boost` service.
- `custom_components/better_thermostat/strings.json` и `translations/*.json` — config/service/sensor текстове.
- `custom_components/better_thermostat/utils/const.py` — публични service/schema/constant имена.

Потребителската документация е в:

- `docs/Configuration/configuration.md`
- `docs/deep-explanations/warm-floor-and-boost.md`
- `README.md`

## 4. Жизнен цикъл

```text
window open commit
    -> tracker.open_temp = cur_temp

window close commit
    -> consume open_temp
    -> проверка на heat/cool threshold
    -> проверка на hvac_modes на Cooler
    -> проверка на cooldown
    -> boost_direction = HEAT или COOL

следващ control cycle
    -> update_boost_trend()
    -> control_queue() избира control_boost(), не control_cooler()
    -> snapshot на Cooler state само при първото реално actuating
    -> задаване на посока, target temperature и optional fan mode

край на Boost
    -> target/tolerance или anticipatory projection, safety timeout,
       contact open/off/no-call или друга cancellation причина
    -> restore на fan mode, HVAC mode и temperature от snapshot-а
    -> tracker.clear(now_ts=...)
    -> следващият cycle отново е normal control_cooler()
```

Важно: `current_mode` в `control_boost()` се поддържа локално и се обновява след команда. Не използвай стария HA state snapshot за restore сравнение — това чупи случая, в който Boost се активира и спира в един и същи cycle.

## 5. Настройки и подразбиращи се стойности

| Ключ | Default | Допустим диапазон | Предназначение |
|---|---:|---:|---|
| `boost_heater_enabled` | `false` | — | Включва функцията; предлага се само при конфигуриран Cooler |
| `boost_threshold` | `5.0 °C` | `0.1–15.0` | Heat drift threshold; при липса на отделен cool threshold служи и за cooling |
| `boost_asymmetric_threshold` | `false` | — | Показва отделна cooling граница |
| `boost_threshold_cool` | `None` | `0.1–15.0` | Cooling drift threshold, когато asymmetric е включен |
| `boost_fan_mode` | `None` | capability-dependent | Optional fan mode; `None` означава да не се променя |
| `boost_lag_minutes` | `5.0` | `0–30` | Residual heat/cooling window за ранно спиране |
| `boost_max_runtime` | `60.0 min` | `5–240` | Safety cap |
| `boost_cooldown_minutes` | `15.0 min` | `0–120` | Минимално време след действително започнал Boost преди нов arm |

При disabled Boost нормализаторът връща чисти defaults и изтрива stale threshold/fan/cooldown стойности. При asymmetric=false `boost_threshold_cool` остава `None`, а close evaluator използва heat threshold-а.

## 6. Условия за спиране и безопасност

- Ако Better Thermostat е `off`, contact е отворен или `call_for_heat is False`, Boost се отменя и се възстановява предишното състояние.
- Ако Cooler е `unknown`/`unavailable`, cycle-ът не пише към него.
- Не се изпраща fan command, ако няма configured fan override или mode-ът не е сред реалните `fan_modes`.
- Не се спира anticipatory, докато температурата е извън tolerance band в грешната посока.
- След влизане в tolerance band се прогнозира `filtered_temp + temp_slope * lag + solar_adjustment`.
- Slow EMA с time constant 1 час предпазва от спиране заради единичен шумен sensor reading; EMA има standing memory още преди Boost да стартира.
- Learned solar gain се добавя като warming contribution: heat boost може да спре по-рано, cool boost има нужда от по-голям margin.
- `boost_max_runtime` винаги прекратява цикъла, дори сензорът да не стига target.
- Cooldown започва само ако Boost е започнал реално да actuate-ва (`boost_started_ts`); недостъпен Cooler не трябва да стартира cooldown clock.

## 7. Snapshot/restore договор

При първия actuating cycle се запомнят:

- предишният `hvac_mode`;
- предишният setpoint, прочетен през `read_setpoint_celsius()`;
- предишният `fan_mode`, само ако Boost ще променя fan mode.

При край се възстановяват буквално тези стойности. Ако предишният режим е `off`, temperature write се пропуска, защото някои integrations отхвърлят setpoint при off. Ако няма валиден snapshot, fallback-ът е да се изпрати `off`.

Това поведение е нарочно различно от обичайния hand-off към `control_cooler()`: Boost не трябва да измисля нова normal decision при restore.

## 8. Ръчно тестване в Home Assistant

Service: `better_thermostat.test_boost`

- target entity: Better Thermostat climate entity;
- `direction`: `heat` или `cool` (default е `heat`);
- изисква Boost да е enabled, Cooler да е конфигуриран и Cooler да поддържа избраната HVAC mode.

Service-ът само arm-ва същия tracker и извиква същия control path; не е отделна тестова имплементация. Използва се за проверка на target entity, mode support, target setpoint, fan mode и restore wiring.

## 9. Тестове

Основните тестови файлове са:

- `tests/unit/test_boost_heater_tracker.py`
- `tests/unit/test_boost_heater_window_reopen.py`
- `tests/unit/test_control_boost_heater.py`
- `tests/unit/test_boost_cooldown_and_asymmetric_threshold.py`
- `tests/unit/test_boost_solar_awareness.py`
- `tests/unit/test_boost_revert_to_previous_state.py`
- `tests/unit/test_boost_control_queue_scenario.py`
- `tests/unit/test_config_flow_boost_heater.py`
- `tests/unit/test_trigger_test_boost.py`
- `tests/unit/test_climate_test_boost_service.py`
- `tests/unit/test_warm_floor_and_boost_status_sensors.py`

На 2026-10-16 targeted suite-ът не може да бъде отчетен като успешно изпълнен на тази Windows среда: стандартният Home Assistant pytest plugin импортва Unix-only `fcntl`, а при изключен autoload async тестовете остават без async plugin. В същия опит sync тестовете преминаха, но async тестовете не са валидирани локално. Следваща надеждна проверка трябва да се пусне в Linux/WSL/CI със съвместим Home Assistant test environment.

## 10. Важни правила за бъдещи промени

1. Boost и `control_cooler()` не трябва да пишат към един и същи Cooler в един cycle.
2. Window open temperature се консумира при close дори когато няма arm; stale reading не трябва да може да заобиколи cooldown.
3. Проверявай реалните `hvac_modes` на Cooler преди arm; unsupported direction не се запомня.
4. Не премахвай exact snapshot/restore без да актуализираш regression тестовете — това беше live-HA проблем и има отделен тестов файл.
5. Не използвай стойност от state snapshot като доказателство за текущия mode след команда, изпратена в същия cycle.
6. Запазвай единиците: вътрешната Boost логика е в Celsius; model fixes трябва да четат/пишат през съществуващите normalization helpers при Fahrenheit системи.
7. При промяна на config keys обновявай едновременно `boost_heater.py`, `config_flow.py`, `climate.py`, `strings.json`, всички translations, service/schema и целевите тестове.
8. При промяна на stop логиката добавяй тестове и за heat, и за cool; solar gain е warming contribution и има различен ефект върху двете посоки.
9. Не добавяй Boost към door semantics — функцията е специфично за прозорец.
10. След всяка промяна обновявай този файл с дата, commit/PR контекст, ново поведение и тестов резултат.

## 11. Текущо състояние на работното дърво

При създаването на този файл вече има несвързани потребителски промени в:

- `README.md`
- `custom_components/better_thermostat/manifest.json`
- `.release-1.9.3.1.2/`

Те не са променяни като част от Boost контекста и трябва да бъдат запазени.

Last reviewed: 2026-10-16
