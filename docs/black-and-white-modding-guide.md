# Black & White: modding and reverse-engineering field guide

Practical findings for investigating the original **Black & White (2001)**:
its executable, script virtual machine, objects, creatures, animations and world
systems. This document can be used independently of any particular mod.

**Names are not proof of behavior.** Check a function, enum entry or asset's
callers, implementation and runtime requirements before relying on its name.

## 1. Identify the exact executable first

The address and offset examples below concern one examined **1.42 x86 build**.
They are not universal across patches, Creature Isle or replacement engines.

The game is native 32-bit C++, with MSVC-style RTTI. It is not a managed .NET
application. The examined executable's image base is `0x00400000`.
Absolute addresses in this guide are virtual addresses; subtract that image base
to obtain an RVA. Object offsets are relative to the named object, not the image.

A matching version string does not establish binary compatibility. Compare PE
headers, sections, executable code and relevant data. Resource-only changes can
leave the engine layout intact, but that needs verification. A matching function
signature alone does not establish that its data dependencies remain compatible.

References consulted for these findings:

- [bw1-decomp](https://github.com/openblack/bw1-decomp): reconstructed declarations,
  including creature-physical and town-desire structures.
- [Daniels118/blackandwhite](https://github.com/Daniels118/blackandwhite): CHL format
  information and symbolic identifiers used when examining quest scripts.
- [openblack](https://github.com/openblack/openblack): enum labels and info-field
  names, including tree-growth and resource-miracle fields. It is a separate
  engine implementation; its runtime layout is not the original executable's.

These references help identify structures and names. The original executable's
instructions and data consumers establish the behavior described here.

Use recovered names as navigation aids. Confirm instruction boundaries,
cross-references, arguments and consumers in the actual target binary. A stale
symbol can point inside a different function. Searching for bytes resembling a
call instruction is not equivalent to decoding a real call.

## 2. The script VM is a useful integration boundary

`Plug Ins/ScriptLibraryR.dll` exposes a relatively small interface around the
script VM. Relevant exports include:

- Lifecycle: `Initialise`, `LoadBinary`, `RestoreState`, `LookIn`, `Reboot`,
  `UnInitialise` and `Version`.
- Tasks: `TaskNumber`, `TaskName`, `GetCurrentScriptType`,
  `GetFirstRunningTaskId`, `GetNextTask`, `FindScript`, `StartScript`, `StopTask`.
- Variables and operands: `LoopGlobalVariables`, `LoopTaskVariables`, `POP`,
  `PUSH` and `STRING`.

A proxy DLL can forward the original interface and observe selected lifecycle
boundaries. Preserve the original DLL and its calling conventions. Keep substantial
initialization outside `DllMain`, and inspect the built PE export table rather
than assuming the linker preserved names and forwarding targets.

### A particularly easy ABI trap: `POP`

The VM's `POP` returns a raw 32-bit word in **EAX**, including when that word
represents floating-point bits. Declaring a wrapper as returning C++ `float`
changes the return convention and can corrupt script execution. Unmodified
operand functions are good candidates for direct export forwarding.

The examined VM wrappers use `__cdecl`; many engine object methods use
`__thiscall`, with `this` in ECX. Check stack cleanup and register usage for each
native call. Do not infer the convention solely from a recovered prototype.

### Native script functions can be discovered from the interface

`Initialise` receives the engine's native-function table. Each examined entry is
144 bytes: a function pointer, input count, output count, reserved word and
128-byte name. This is a better discovery point than inventing a list of names
or searching the entire executable on every update.

Native entry/exit callbacks can identify the function slot and running script.
Copy VM-owned names while they are valid. **Do not call `POP` to inspect operands**:
that consumes the VM stack. A callback exit also does not prove that a requested
world effect succeeded.

Loading, restoring, rebooting and shutting down the VM provide useful boundaries
for invalidating runtime caches. Creature replacement, island transitions and
simulation-clock rollback need similar treatment.

## 3. Reading objects without inventing their meaning

### A few useful root pointers

These anchors belong to the examined 1.42 build. Read pointers at runtime;
the addresses of their allocated objects change between sessions.

| Location | Meaning |
| --- | --- |
| `0xD0195C` | Address containing the current game pointer |
| Game `+0x18`, stride `0xA60` | Player array, with eight slots in the examined layout |
| Game `+0x205A59` | Selected player index |
| Game `+0x205A40` | Simulation tick |
| Player `+0xA4C` | Registered creature pointer |
| Creature `+0x1070` | Owner pointer |
| Creature `+0x160` / `+0x164` | Physical / mental object pointers |
| CreaturePhysical `+0x28` | Held-object pointer |

### RTTI and inheritance

For the examined MSVC x86 layout:

| Location | Meaning |
| --- | --- |
| First word of an object | Vtable pointer |
| `vtable - 4` | Pointer to Complete Object Locator |
| Locator `+0x0C` | TypeDescriptor pointer |
| TypeDescriptor `+8` | Decorated class name, such as `.?AVCreature@@` |
| Locator `+0x10` | ClassHierarchyDescriptor pointer |

Check mapped memory before following these pointers. A recognizable class name
does not prove that a pointer is the primary base or that a virtual call needs
no `this` adjustment.

`GameThingWithPos` is not interchangeable with `Object`. For example, the
`Citadel` and `Flock` containers have positions but do not satisfy the ordinary
physical-object contract. `CitadelHeart` is a physical object. This distinction
affects movement, targeting and virtual calls.

Cache type information by vtable where its lifetime is established. Do not treat
an object address as a permanent identity: objects disappear and addresses can
be reused after loading, destruction or replacement.

### World coordinates

The common `GameThingWithPos` position uses:

| Offset | Representation |
| --- | --- |
| `+0x14` | X, signed 32-bit fixed point |
| `+0x18` | Z, signed 32-bit fixed point |
| `+0x1C` | Altitude, floating point |

Convert horizontal coordinates with `world = raw * 10 / 65536`.
Other structures use floating-point vectors in X/Y/Z order; the hand's position
must not automatically be decoded as this fixed-point structure.

Moving to an object's exact centre is different from approaching it. A building's
centre may be inside its collision footprint. Native object-approach routines can
account for stopping distance; a coordinate move cannot automatically substitute
for every interaction.

### Creature knowledge is not a global world index

`CreatureMental + 0x2208` contains `CreatureBeliefs`; examined belief-list headers
occur at `+0x2210` and `+0x2224`, relative to the mental object. These lists describe
what that creature knows. They are not guaranteed to contain everything beneath
the camera or god hand. Engine-owned object lists are needed for independent
world discovery.

Belief lists can grow substantially as an island is explored. A limit of 512 is
not an established engine capacity. Validate list length, links, cycles and
object identities; make any reader limit explicit instead of presenting a
truncated result as the complete world.

### Class names often need subtype information

| Object family | Useful distinction |
| --- | --- |
| Divine creatures | `Creature` plus `CreatureInfo` identifies species. Do not confuse these with ordinary animals of the same species. |
| Wildlife | Concrete types include `Cow`, `Sheep`, `Horse`, `Pig`, `Wolf`, `Tortoise`, `Crow`, `Dove` and `Pigeon`; a generic animal label loses available information. |
| Toys | Some are generic `MobileStatic` objects. In `GMobileStaticInfo`, mesh at `+0x120` distinguishes ball 399, bowling ball 400, cuddly toy 401, die 402 and skittle 403. Separate ball classes also exist. |
| Mushrooms | Identification spans mobile-object subtype data and feature data. There is no single universal mushroom class or reliable colour-only rule. |
| Scaffolding | `Scaffold` is distinct from an existing building and can hold a planned-building preview. |
| Fishing areas | `FishFarm` describes a fishing location, not one carried fish or a food quantity. |
| Animal groups | `Flock` is a container, not an individual animal. |

The creature info pointer is at `Creature +0x28`, with the examined species name
at `CreatureInfo +0x18`. Resolve through the correct type and data layout rather
than guessing species from owner, model size or an object ID.

## 4. Creature physiology: misleading labels and useful measurements

The game-facing condition meters do **not** share the same direction as their
internal sources:

| Player-visible meter | Internal source | Display conversion | Worse condition |
| --- | --- | --- | --- |
| Damage | Health at `Creature +0x48` | `(1 - health) * 100` | Higher Damage |
| Hunger | Energy at `CreaturePhysical +0x1C` | `(1 - energy) * 100` | Higher Hunger |
| Tiredness | Exhaustion at `CreaturePhysical +0x30` | `exhaustion * 100` | Higher Tiredness |

These mappings were traced through the display and statistics paths. Useful
anchors are `GameStats::GetCreatureHunger` at `0x565450` and
`GetCreatureTiredness` at `0x565480`. Displaying these as integer percentages
matches their purpose; preserve raw values separately when investigating them.

**Energy is a food-related reserve, not the inverse of tiredness.** Energy and
exhaustion are independently updated. Movement increases exhaustion, and low
energy can increase that cost. Action updates can change both values. Even the
name `HasEnoughEnergyToCastSpell` is misleading: the examined check at `0x4F82F0`
compares exhaustion against a spell-cost-dependent limit. Spell maintenance at
`0x4F8350` separately spends energy and adds exhaustion.

The creature's desire to eat is another value again. `CreatureDesires` contains
forty motivation scores, including hunger, curiosity, play, fear, rest and social
urges. They are not HUD percentages and not interchangeable with physical
measurements. A high illness-named motivation is not by itself a diagnosed illness.

Other readable physical fields include strength `+0x0C`, warmth `+0x10`, fatness
`+0x14`, itchiness `+0x20`, dehydration `+0x34` and age `+0x08`, relative to
`CreaturePhysical`. Do not attach human units or severity thresholds until their
consumers are understood. A field named warmth is not automatically degrees Celsius;
creature age is not automatically human years.

There is native evidence connecting the Ill, Thirsty and Itchy creature spells
to illness, water and scratch drives respectively. That does not settle every
ordinary reading of those drives. `manifest_state` and `mental_illness` remain
unresolved leads; their names do not prove active gameplay systems.

Sleep also has phases. A sleep intention or journey home is not sleeping.
The examined sleep controller distinguishes falling asleep (2), asleep (3) and
waking (4), alongside the body-animation state. Keep intentions, current plans
and current physical state separate.

Training feedback also has an event identity. The examined interaction magnitude
is a float at `CreatureMental +0x18C5C`, followed by its engine tick. Positive and
negative magnitudes represent praise and punishment. Watching or zooming in on
the creature is attention, not automatically praise. Detect a new feedback tick
rather than repeatedly reporting an unchanged magnitude, and establish a baseline
after loading instead of replaying old feedback.

## 5. Executing behavior: plans, targets and results

The creature's high-level plan registry is at `0x9D1698`, with examined records
80 bytes apart. Trace the registered constructor, not merely a similarly named
function elsewhere. Inspect its primary and secondary target reads, subactions,
preconditions and return paths.

Several independent ID spaces exist:

- High-level plans/agendas.
- Subactions inside a plan.
- Individual body animations.
- Concurrent body animations.
- Facial animations and controller states.

An integer from one namespace cannot safely be reused in another.

### Useful behavior entry points

These are binary-specific constructor anchors, not a universal callable API.
Arguments and execution context still need verification.

| Native behavior | Plan | Constructor | Finding |
| --- | --- | --- | --- |
| `DrinkFromTheSea` | 55 | `0x4A15F0` | Finds natural water and builds movement plus drinking. |
| `FishAndEat` | 155 | `0x4AB350` | Selects fishing water itself; supplying an agenda target does not bind it to that fishing area. |
| `TakeFishHome` | 259 | `0x4BA4B0` | Catch-and-carry sequence; destination and shoreline preparation require attention. |
| `PutDown` | 67 | `0x4A3720` | Native held-object release. |
| `EatObjectInHand` | 276 | `0x4BE4E0` | Held-object eating path. |
| `ExamineObjectInHand` | 275 | `0x4BE460` | Constructs held-item examination, normally using animation 103. |
| `BringHome` | 84 | `0x4A5740` | Generic held-object delivery to the creature's home. |
| `BringFoodHome` | 164 | `0x4AEBD0` | Food-specific delivery constructor. |
| `TakeToyHome` | 322 | `0x4C2BB0` | Toy-specific delivery constructor. |
| `GoToTeleport` | 134 | `0x4A9120` | Needs teleport reaction and destination setup, not merely walking. |

`DrinkFromTheSea` is another misleading name: its water search can find a river.
The examined search at `0x47D860` starts with a 1,000-world-unit search distance.
The name alone does not establish how drinking affects physical needs.

Fishing-to-eat and fishing-to-deliver are different sequences. The native delivery
path includes approach, catch creation, pickup, carrying and release. Its
constructor omits a shoreline-position correction used by `FishAndEat`; blindly
calling the constructor can therefore reach the fishing area and stop without
catching anything. Trace the approach preparation as well as the delivery sequence.
Autonomous behavior can resume after release; subsequent movement does not by
itself mean the delivery is still running.

Home delivery uses the creature's native home/pen coordinates at
`Creature +0x1200`, rather than the centre of the temple object. The separately named
`HasNoHome` field at `+0x11FC` was not a reliable blanket precondition: valid home
coordinates and delivery were possible despite a misleading interpretation of
that field.

### Target eligibility and interruption

Prefer the engine's eligibility predicates to a hand-written list of “obviously”
edible or pickable objects. In the examined layout, relevant virtual slots include
pickup at `+0x258` and eating at `+0x22C`. Validate the function and calling contract
before invoking either. Size, reach, current state and object type can all matter;
“the creature is small” is not a sufficient replacement for the native test.

Held objects, target objects and destination objects occupy different roles.
Casting a held miracle or throwing at something needs the correct constructor's
target arrangement. An action's name does not establish whether its recipient is
read from the primary target, secondary target or a separate field.

Use the native agenda transition/cleanup path where possible. Writing only a
current-plan number can leave old reactions, targets or subactions alive.
Keep script control, cutscenes, combat and leash intervention distinct from
ordinary autonomous reselection. A leash takeover during a delayed command can
invalidate that command even if the eventual response is otherwise valid.

Track these stages separately: **request accepted, plan built, plan started,
animation observed, plan ended, effect confirmed**. A constructor can return
success with no useful sequence, and a plan can end because it was interrupted.
Do not infer an effect merely from a timeout or plan change.

## 6. Animations and recoverable behavior

### Individual body animations

The examined `Data/ctrspec27.txt` agrees with native callers for these individual
body animation IDs:

| ID | Gesture |
| --- | --- |
| 217 | Howl |
| 218 | Laugh |
| 219 | Cry |
| 220 | Realization / “aha” |
| 221 | Pray |

These are individual-body IDs. The nearby range 200–204 belongs to a different
concurrent-animation namespace; it cannot substitute for this table. Older
creature specifications and adviser animation tables also differ.

Useful anchors are `StartIndividualAction` at `0x484B80`, `GetAnim` at `0x619650`
and `GetSetAnim` at `0x619690`. Do not invent an index adjustment to reconcile
unrelated tables. Check species-specific animation assets before assuming identical
appearance or sound across creatures.

Held-item body slots include **100 stroke, 101 shake, 102 smell and 103 examine**.
These require a held object; they are not generic empty-hand emotes. The same
object can be inspected through several animations without changing the underlying
item information.

Gentle placement and discarding are separate too: the examined release subaction
uses animation 97 for placement and 95 for a discard with engine-supplied velocity.
Neither should be confused with an aimed throw at a target.

### Facial animation dispatch

Face IDs such as 16 (`Nsmile`), 20 (`Nsad`) and 23 (`Nlaugh`) are not body IDs.
`StartFacialAction` at `0x480C60` can accept a desired face and timer without proving
that a usable clip played. The consumer at `0x480D40` retrieves and advances the
actual facial animation. Check that consumer and the available clip; a body
animation and a face-only animation use different playback paths.

### The mushroom sequence exists

The native eating routine at `0x500540` can queue a mushroom callback at `0x509530`.
The callback builds an eye, eyelid, facial and body-animation sequence.

`GetHigh` plan 181 is not itself a reliable “currently intoxicated” flag. The name
also covers obtaining/eating a mushroom. The effect sequence can instead be
recognized by its subaction structure and eye-controller changes. `LookStoned`
at `0x4D10D0` sets eye mode 3; eyelid behavior has a separate path at `0x4D1220`.

For a recovery hook, observe a new consumption event, let eating finish, and
invoke the native effect once. Establish a fresh counter baseline after loading;
a saved lifetime count is not a new event. Recognize an already-running or pending
native effect so that a hook does not duplicate it.

## 7. Miracles, learning and prayer power

Keep the physical bubble, held spell and dispenser distinct:

| Runtime type | Meaning |
| --- | --- |
| `OneOffSpellSeed` | Physical one-shot miracle bubble |
| `SpellSeed` | Activated spell representation |
| `SpellIcon` and dispenser objects | Sources or interface objects associated with miracles |

For the examined layout, `OneOffSpellSeed +0x68` holds the seed index and `+0x78`
the power level. `SpellSeed +0x58` points to its info and `+0x6C` holds the power
level. Resolve the spell through `GSpellSeedInfo`; do not identify it from the
bubble's generic class alone. The seed-info table is at `0xD9D678`, with entries
`0x190` bytes apart. Base spell is at `+0x124`; the power-variant table starts at
`+0x128`. Handle the native sentinel for falling back to the base spell.

Knowledge, required knowledge, current casting capacity and battle availability
are different values. The examined learning requirement combines spell data with
a species-specific factor. Changing creature species can therefore raise the
requirement above retained knowledge. Missing battle spells can simply mean they
have not been learned; do not attribute that automatically to a tutorial lock.

Learning values and their timestamps also have different lifetimes. A retained
learning timestamp may exceed a newly reset island tick. That does not invalidate
the stored knowledge value.

Food miracles create edible resources and can supply storage. They are not a
crop-growth accelerator. Water and tree-growth paths are separate mechanics.
Prayer-power readings belong to worship-site/player systems; distinguish local
available power, totals and scripted overrides rather than treating any one float
as a universal global mana value.

## 8. Towns, gods, villagers and scaffolding

### Ownership is not belief

Player storage contains multiple slots, including inactive and neutral entries.
An initialized slot, default belief threshold or generic `Player[n]` name is not
evidence that a god is present. Use active-player state, neutral-player identity,
alliance data and registered creature links.

A scripted creature can have an engine owner without representing that god in
the story. The tutorial bear and similar actors should not be assigned a divine
identity solely from an owner pointer. Human-player hand and computer-player hand
positions also use different structures; a temple position is not a substitute
for an unreadable hand.

Each town has an owner, tribe and per-god belief data. A neutral town can already
have belief in a god. Matching a conversion reference does not prove control has
transferred: check the owner change. Buildings can lead back to their town through
the native town getter, but different building classes use different field layouts.

### Storage desire flags versus internal town desires

The internal town-desire array has 17 categories, including entries such as
sleep, relaxation and wonder. These are not all visible storage flags.
To reproduce the needs shown by flags, inspect the actual `TownDesireFlags`
objects referenced by the town, rather than publishing every internal score as
a visible request.

| Examined location | Meaning |
| --- | --- |
| `Town +0x9B0` | Array of 17 flag pointers |
| Flag `+0x5C` | Desire category |
| Flag `+0x64` | Owning town |
| `Town::GetRawDesire`, `0x73E420` | Sums base, temporary and modifier values |

For category `i`, that raw sum reads floats at town offsets
`0x19C + 4*i`, `0x108 + 4*i` and `0xC4 + 4*i`. Include categories backed by actual
flags and validate their owner/category. The visual flag applies smoothing and
clamping, so the raw score is not an exact flag-height measurement.

### Villagers

Current activity, scheduled continuation, assigned profession and scripted-actor
status are separate data. In the examined layout, current activity is the byte
at `+0x8C`; the neighboring state must not be reported as the action happening now.
States 13/14 represent dying and 15 dead. An actor flag at `+0x25`, mask 4, and
scripted activity states can identify quest/script participants beyond one special
villager subclass.

Assigned role is available at `+0xF2`; sex comes from `GVillagerInfo +0x1F8`.
Villager age can be derived from birth tick and the engine's ticks-per-year value,
with unsigned tick arithmetic. This is a separate clock/layout from creature age.
A name getter returning null is missing information, not permission to fabricate
a personal name.

### Scaffolding

A carried scaffold can expose a planned building before placement. The examined
primary planned-abode pointer is at `+0x6C`, with an alternative at `+0x70`.
Validate these separately; a preview is not a constructed building.

Scaffold scale at `+0x50` encodes combined scaffold count:
`count = round((scale - 0.5) * 5)`, corresponding to the setter
`scale = count * 0.2 + 0.5`. It is not construction progress. Planned-building
information can provide description, tribe and required scaffolds.

## 9. Weather, ground cover and burning

Falling rain, falling snow and existing snow on the ground are separate systems.
Dry weather does not mean snow-free terrain.

The examined precipitation cache is a 128-by-128 grid with 40-world-unit cells.
Its pointer is at `0xEDC350` and current generation at `0xEDC340`. Cell rain/snow
values use signed bytes at offsets 1/2, with generation at offset 7. A stale cell
is unavailable information, not a current zero. A passive reader does not refresh
the engine's cache simply by reading it.

Ground snow has a separate snow-cover grid at the pointer stored at `0xEDC344`,
plus terrain-material information. Snow material index 27 is relevant. Trace the
terrain-material sampler when deriving a ground-snow flag; precipitation alone
cannot provide it. No equivalent persistent “rain on the ground” boolean has
been established.

Time of day is an engine value with phase thresholds, not the computer's wall
clock. Useful anchors are the value at `0xBF3380` and thresholds at `0xFA2694`.

For ordinary objects using the examined `IsOnFire` implementation (`0x402AA0`,
virtual slot `+0x298`), burning involves a fire object linked at `Object +0x44`,
an owner check and a fire-level threshold. It is not merely “fire pointer nonzero.”
Different implementations need separate validation. Missing fire metadata must
remain unknown rather than being silently labelled not burning.

Bonfires deserve their own checks. Their class and eligibility paths differ from
ordinary trees or food, and god-hand pickup restrictions do not automatically
describe creature pickup. Creature pickup/eating and extinguishing behavior should
not be inferred from inheritance alone.

## 10. Vortex transfers and teleport networks

### Vortex: transfer to another island

`LandscapeVortexIn` and `LandscapeVortexOut` belong to island-transition machinery.
`LandscapeVortexVolc` is a different object and should not automatically be
classified as a usable entrance.

The examined vortex list is at `0xD38220`. An entrance's `+0xEC` points to
`VortexSave`, whose `+0x18` holds the active filename pointer. The byte at `+0x1C`
distinguishes the transfer-writer mode; it is not a general lock proving that the
file is idle. Follow the active object to locate the export instead of guessing
among similarly named profile files.

The transfer text, commonly `vortex.txt`, uses records such as `CREATE_NEW_TREE`,
`CREATE_NEW_ANIMAL`, `CREATE_TOWN_VILLAGER`, `CREATE_MOBILE_STATIC`, `CREATE_POT`
and `CREATE_ONE_SHOT_SPELL_PU`. Parse arguments as data, including quoted strings;
never evaluate the file as code. A partially written final line is possible.

For the four-argument `CREATE_POT` form, the third argument is resource type
(0 food, 1 wood) and the fourth is quantity. Other records have their own schemas.
An export record alone does not prove successful import: the receiving island
must also accept its type and arguments.

The game can rewrite the export while accepting deposits. Editing safely requires
a backup, a known inactive writer, and a recheck that the file has not changed.
Preserve existing deposits, avoid duplicate insertion and test additions in bounded
batches rather than assuming arbitrary quantities will load well.

### Teleport: travel within one island

`MagicTeleport` gates are a separate system. The examined `GPlayer` has a network
head at `+0xA58` and count at `+0xA5C`; gates link through `+0x88` and carry their
owner at `+0xA0`. The network can contain more than two gates. Do not reduce it
to fixed entrance/exit pairs.

Walking to a gate is insufficient for creature travel. `ProcessTeleports` can clear
the saved destination unless the creature's active reaction matches the entrance's
reaction. The entrance reaction pointer is at `+0x94`; the examined reaction refers
back to its source at `+0x14` and has kind 20 at `+0x24`. Travel requires both
the entrance reaction and the onward destination to be set correctly.

Revalidate both gates before use: gates can expire and a destination must differ
from the entrance. Per-player network storage alone does not establish the rules
for access through another god's gates.

## 11. Resource-production and growth entry points

These entry points control different parts of resource production and growth.

| System | Entry point / data | Distinction to preserve |
| --- | --- | --- |
| Individual tree growth | `Tree::Process`, `0x74A290`; `GTreeInfo +0x118`, `+0x11C`, `+0x130` participate in timing, growth and precipitation response | Changing a tree's growth is not the same as creating more trees. |
| Water applied to trees | `0x74C390` | Separate path from ordinary growth. |
| Forest regeneration | `Forest::Process`, `0x539DA0`, with selection at `0x539FD0` | Forest-level behavior needs its own investigation. |
| Resource miracles | `SpellResource::SpellEvent`, `0x724D80`; info fields `+0x60`, `+0x64`, `+0x68` | Initial amount, repeated amount and cost-per-unit are separate; production also uses tribal power. |
| Wood creation | `0x724D30` | Resource creation path, not tree growth. |
| Workshop scaffold production | `0x7797F0`, production start `0x7798A0`; `GScaffoldInfo +0x6C` | Wood cost for making a scaffold differs from combined-scaffold count and final building requirements. |

Available prayer power is not a direct universal multiplier for Wood output.
Cost and spell lifetime can affect how long production continues. A one-shot
bubble still selects spell data, scale and lifetime; there is no verified universal
“one cast equals one building's wood cost” relationship.

Trace all consumers of a shared info field before changing it. An apparently
local cost or size can also affect valuation, construction, display or another
object's behavior.

## 12. Investigating scripts and apparently unused content

The examined `challenge.chl` uses LHVM version 7. Script bytecode, native engine
functions, localized text and assets need to be studied together. Localized
`InfoScript2.txt` is UTF-16; reading it with the wrong encoding can hide search
matches or create meaningless text.

The island-one sailor quest illustrates several traps:

- `LandControl1` starts `TheMissionaries`. Three song-verse scripts, audio assets
  and their subtitle lines are connected. An additional unused verse was not found.
- Passenger/replacement-sailor branches exist. Conditions that make a branch rare
  are different from a branch being unimplemented.
- The starting boat uses `FEATURE_INFO_ARK_DRY_DOCK` with protection flags. Removing
  those flags does not establish a complete cut boat-destruction quest.
- A later-island return script uses `FEATURE_INFO_ARK_WRECK`; a wreck asset is not
  automatically unused just because it is not seen in the first quest.
- Locals such as `NewArk` and `Repaired`, initialized without meaningful later use,
  are research leads, not proof of a recoverable repair quest.

Likewise, text with no obvious constant reference might be selected dynamically
or by native code. Search references and actual conditions before declaring it
unreachable. Record the distinction between unused data and working code with a
missing or restricted trigger.

## 13. Keeping a mod's runtime hooks and readers fast and reliable

This section concerns work added by a mod: in-process hooks, external readers,
data collection and overlays. Repeating that work every frame can cause slowdowns
even without a memory leak. Long sessions also enlarge belief lists and the world
area already explored.

- **Measure the hook itself.** Keep bounded timing counters for scanning, object
  validation and snapshot construction. Compare the same running game with each
  subsystem enabled and disabled.
- **Stop idle scans.** Closing an external frontend does not unload a DLL already
  inside the game. Explicitly stop expensive work when there is no active reader.
- **Separate sampling rates.** Input/control and pending actions may need frequent
  updates; static object metadata and full surroundings scans usually do not.
- **Batch reads and reuse verified metadata.** Repeated memory queries, RTTI walks
  and getter checks for every field of every object can dominate the cost.
- **Keep caches scoped correctly.** Immutable type metadata is a better cache
  candidate than health, ownership, held objects or action eligibility. Invalidate
  session-dependent data on load and replacement.
- **Bound every queue and recording.** Use ring buffers and fixed retention rather
  than indefinitely appending events, snapshots or unique object addresses.
- **Avoid unnecessary overlay redraws.** Rewriting unchanged text or recreating
  controls on every poll can cause redraw, scrolling and caret churn. Update only
  changed state.

A coherent read does not require the entire world to remain frozen. Check the
identity and structural links that make the sample meaningful; record moving
values with a timestamp. Rejecting a whole surroundings scan whenever any position,
statistic or simulation tick changes can make busy towns appear unreadable.
Failure to read one optional attribute should not erase every otherwise valid
object in the sample.

Run engine methods at a verified game-thread boundary. An external reader or
background command receiver should not call native object methods concurrently
with simulation. Revalidate mutable targets at dispatch, even if they were valid
when a command was first requested.

If lag disappears in a menu, remember that the simulation and many hooks also
stop or change cadence there. That observation alone does not identify rendering
as the cause. Similarly, a fresh process removes accumulated game state as well
as instrumentation state, so it is not a controlled comparison by itself.

## 14. A repeatable investigation checklist

1. Record the binary identity, address/RVA, object type and data representation.
2. Trace the relevant writer and consumer, not only the field or symbol name.
3. Establish the runtime preconditions: ownership, held item, learned ability,
   script control, reaction, animation namespace and destination.
4. Start with a read-only observation, then test one bounded action at a time.
5. Record a small before/during/after sample and compare it with visible behavior.
6. Distinguish constructor success, playback and actual world effect.
7. Repeat across a meaningful different condition, such as inland versus shore,
   another species, a loaded save or a different island.
8. Keep unresolved hypotheses labelled. Publish original explanations and minimal
   technical facts rather than game binaries, saves, raw captures or copied assets.

The recurring lesson is to follow what the engine **does with a value**. Names
such as energy, hunger, `DrinkFromTheSea` and `GetHigh` can suggest the wrong
implementation or the wrong observable meaning.
