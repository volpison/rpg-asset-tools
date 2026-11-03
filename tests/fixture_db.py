"""A tiny data-only RPG Maker MZ project with KNOWN database references (for dbscan tests)."""
import json
from pathlib import Path

C = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def _list(items):
    return "[\n" + ",\n".join(C(i) for i in items) + "\n]\n"


def cmd(code, *params):
    return {"code": code, "indent": 0, "parameters": list(params)}


def build(root: Path, dynamic: bool = False) -> Path:
    root = Path(root)
    for d in ("data", "js/plugins"):
        (root / d).mkdir(parents=True, exist_ok=True)

    def w(name, items):
        (root / "data" / name).write_text(_list([None] + items))

    w("Items.json", [
        {"id": 1, "name": "Potion", "effects": [], "animationId": 0},                  # shop
        {"id": 2, "name": "Elixir", "effects": [], "animationId": 0},                  # UNUSED
        {"id": 3, "name": "Key", "effects": [], "animationId": 0},                     # condition
        {"id": 4, "name": "Drop", "effects": [], "animationId": 0},                    # enemy drop
        {"id": 5, "name": "ScriptItem", "effects": [], "animationId": 0},              # $dataItems[5]
        {"id": 6, "name": "PlugItem", "effects": [], "animationId": 0},                # plugin @type item
        {"id": 7, "name": "", "effects": [], "animationId": 0},                        # blank slot
        {"id": 8, "name": "OffItem", "effects": [], "animationId": 0},                 # disabled plugin
        {"id": 9, "name": "CmdItem", "effects": [], "animationId": 0},                 # plugin command arg
        {"id": 10, "name": "Tonic", "effects": [{"code": 21, "dataId": 2, "value1": 1, "value2": 0},
                                                 {"code": 43, "dataId": 6, "value1": 0, "value2": 0},
                                                 {"code": 44, "dataId": 5, "value1": 0, "value2": 0}],
         "animationId": 0},                                                             # unused itself; refs state2, skill6, CE5
    ])
    w("Weapons.json", [
        {"id": 1, "name": "Sword", "traits": [], "animationId": 0},                     # actor equip
        {"id": 2, "name": "Club", "traits": [], "animationId": 0},                      # UNUSED
        {"id": 3, "name": "Wand", "traits": [{"code": 35, "dataId": 5, "value": 0}], "animationId": 2},
    ])
    w("Armors.json", [
        {"id": 1, "name": "Shield", "etypeId": 2, "traits": []},                        # actor slot 1
        {"id": 2, "name": "Spare", "etypeId": 2, "traits": [{"code": 13, "dataId": 4, "value": 1}]},  # UNUSED, refs state 4
    ])
    w("Skills.json", [
        {"id": 1, "name": "Attack", "effects": [], "animationId": -1},
        {"id": 2, "name": "Guard", "effects": [], "animationId": 0},
        {"id": 3, "name": "Fire", "effects": [], "animationId": 1},                     # class learning; anim 1
        {"id": 4, "name": "Unlearned", "effects": [], "animationId": 0},                # UNUSED
        {"id": 5, "name": "Bite", "effects": [], "animationId": 0},                     # enemy action + weapon trait
        {"id": 6, "name": "Taught", "effects": [], "animationId": 0},                   # only via item 10 (itself unused)
    ])
    w("States.json", [
        {"id": 1, "name": "Death", "traits": []},
        {"id": 2, "name": "Poison", "traits": []},                                      # item 10 effect
        {"id": 3, "name": "Sleep", "traits": []},                                       # UNUSED
        {"id": 4, "name": "Regen", "traits": []},                                       # armor 2 trait
    ])
    w("Actors.json", [
        {"id": 1, "name": "Hero", "classId": 1, "equips": [1, 1, 0, 0, 0], "traits": [], "note": ""},
        {"id": 2, "name": "Mage", "classId": 2, "equips": [0, 0, 0, 0, 0], "traits": [], "note": ""},
        {"id": 3, "name": "Ghost", "classId": 1, "equips": [0, 0, 0, 0, 0], "traits": [], "note": ""},
    ])
    w("Classes.json", [
        {"id": 1, "name": "Fighter", "learnings": [{"level": 1, "note": "", "skillId": 3}], "traits": []},
        {"id": 2, "name": "Wizard", "learnings": [], "traits": []},
        {"id": 3, "name": "Spare class", "learnings": [], "traits": []},               # UNUSED
    ])
    w("Enemies.json", [
        {"id": 1, "name": "Slime", "actions": [{"skillId": 5, "conditionType": 0, "conditionParam1": 0,
                                                  "conditionParam2": 0, "rating": 5}],
         "dropItems": [{"kind": 1, "dataId": 4, "denominator": 1}, {"kind": 0, "dataId": 1, "denominator": 1}],
         "traits": [], "note": ""},
        {"id": 2, "name": "Orphan", "actions": [], "dropItems": [], "traits": [], "note": ""},   # UNUSED
    ])
    w("Troops.json", [
        {"id": 1, "name": "Slimes", "members": [{"enemyId": 1, "x": 0, "y": 0, "hidden": False}],
         "pages": [{"conditions": {"switchValid": True, "switchId": 6, "actorValid": False, "actorId": 1},
                    "list": [cmd(0)], "span": 0}]},
        {"id": 2, "name": "Unused troop", "members": [], "pages": []},                 # UNUSED
        {"id": 3, "name": "Cmd troop", "members": [], "pages": []},                    # command 301
    ])
    w("CommonEvents.json", [
        {"id": 1, "name": "CE_Called", "switchId": 1, "trigger": 0, "list": [cmd(0)]},
        {"id": 2, "name": "CE_Unused", "switchId": 1, "trigger": 0, "list": [cmd(0)]},          # UNUSED
        {"id": 3, "name": "CE_Auto", "switchId": 9, "trigger": 1, "list": [cmd(0)]},            # reads switch 9
        {"id": 4, "name": "CE_Script", "switchId": 1, "trigger": 0, "list": [cmd(0)]},
        {"id": 5, "name": "CE_Effect", "switchId": 1, "trigger": 0, "list": [cmd(0)]},          # via item 10 effect
    ])
    w("Animations.json", [
        {"id": 1, "name": "Hit"}, {"id": 2, "name": "WeaponAnim"},
        {"id": 3, "name": "UnusedAnim"}, {"id": 4, "name": "CmdAnim"},
    ])
    (root / "data/System.json").write_text(C({
        "switches": [""] + [f"S{i}" for i in range(1, 11)] + ["", ""],
        "variables": [""] + [f"V{i}" for i in range(1, 8)] + [""],
        "partyMembers": [1], "testBattlers": [], "testTroopId": 0,
        "gameTitle": "Fixture"}) + "\n")

    script = ("$gameParty.gainItem($dataItems[5], 1);\n"
              "$gameSwitches.setValue(8, true);\n"
              "$gameTemp.reserveCommonEvent(4);\n"
              "if ($gameVariables.value(7) > 0) {")
    cmds = [
        cmd(121, 1, 1, 0), cmd(121, 2, 2, 0), cmd(121, 5, 6, 0),
        cmd(111, 0, 1, 0), cmd(0),
        cmd(401, "Hello \\V[1] \\N[2]"),
        cmd(122, 1, 1, 0, 0, 5), cmd(122, 2, 2, 0, 0, 1), cmd(122, 5, 5, 0, 1, 6),
        cmd(111, 1, 3, 0, 0, 0), cmd(0),
        cmd(302, 0, 1, 0, 0, False),
        cmd(111, 8, 3), cmd(0),
        cmd(117, 1),
        cmd(129, 2, 0, False),
        cmd(301, 0, 3, False, False),
        cmd(212, -1, 4, False),
        cmd(355, script), cmd(655, "$gameVariables.setValue(7, 1); }"),
        cmd(357, "DbPlug", "give", "Give", {"item": "9"}),
        cmd(111, 8, 99), cmd(0),                                      # item 99 does not exist
        cmd(0),
    ]
    if dynamic:
        cmds.insert(0, cmd(355, "$gameSwitches.value($gameVariables.value(1));"))
    page = {"conditions": {"switch1Valid": True, "switch1Id": 3, "switch2Valid": False, "switch2Id": 1,
                           "variableValid": False, "variableId": 1, "variableValue": 0,
                           "itemValid": False, "itemId": 1, "actorValid": False, "actorId": 1,
                           "selfSwitchValid": False, "selfSwitchCh": "A"},
            "list": cmds}
    m = {"data": [0, 0, 0], "encounterList": [{"troopId": 1, "weight": 10, "regionSet": []}],
         "events": [None, {"id": 1, "name": "EV", "note": "", "x": 12, "y": 8, "pages": [page]}]}
    (root / "data/Map001.json").write_text(C(m) + "\n")
    (root / "data/MapInfos.json").write_text(_list([None, {"id": 1, "name": "Town", "parentId": 0}]))

    (root / "js/plugins/DbPlug.js").write_text("""/*:
 * @target MZ
 * @param Sw
 * @type switch
 * @param Item
 * @type item
 * @param Num
 * @type string
 * @command give
 * @arg item
 * @type item
 */
""")
    (root / "js/plugins/OffDb.js").write_text("""/*:
 * @param Sw
 * @type switch
 * @param Item
 * @type item
 */
""")
    plugins = [
        {"name": "DbPlug", "status": True, "description": "", "parameters": {"Sw": "7", "Item": "6", "Num": "12"}},
        {"name": "OffDb", "status": False, "description": "", "parameters": {"Sw": "10", "Item": "8"}},
    ]
    (root / "js/plugins.js").write_text("var $plugins =\n" + _list(plugins).rstrip() + ";\n")
    return root
