# limbus-skins 1.6.0

Skins never touch the game's own files
- **The leak is fixed.** Before, a session copied the skins over the game's own downloaded files
  and only put the originals back when it ended properly - so a closed window, a crash or a
  reboot left them in the official game too. Now the game's files are never changed: the skins
  go in a `modcache` folder beside the private server's client patch, and the patch has the game
  read them from there, in that session alone. However a session ends, the official game only
  ever sees its own files.
- **Healed on the spot:** every limbus-skins command first puts back what an older version left in
  the game's files (the original, or - if that was lost - the entry removed, so the game downloads
  its own again). Run any command once, e.g. `limbus-skins status`, with the game closed.
- Needs the Limbus Private Server package **app-1.118.0-2** or later (its client patch reads the
  `modcache` folder); with an older one, limbus-skins puts no skins in and says so.
  `limbus-skins apply` now takes the client patch folder: `apply --client <folder>`.
- **Every skin's pfp is its idle sprite**, and the lists (sinner and identity select) ask the
  client patch for them too.
- **No Black Forest:** nothing waits for it; every boss, ally and kit is open from the start.
- Kali is for the developer's own machine only.

# limbus-skins 1.5.0

44 allies, each with its villain's own face
- **Every repaint and helper skin of a boss or villain is now also an identity of its own**
  (`<skin>-id`, 44 in all - the Queen of Hatred, Kali, the Red Mist, Vergilius, Rien, Dongbaek,
  Starbuck, Captain Ahab, L'Inamovible, ...): equipped like any other, with the boss's own body
  and whole moveset, its records untouched. The sinner's own identity keeps its look.
- **Covers**: each ally's card - full art, info, the profile picture lists show, and the support
  portrait - is drawn from its villain's story portrait in your own Limbus install, framed on the
  face. Needs the private server's client patch from app-1.118.0 or later to show in game.
- **True damage**: a boss with one animation for several moves still deals every tier (Rien), and
  moves written as Library of Ruina pages (Roland's, Kali's) deal those pages' own dice, read from
  your install.
- **Only the strongest are locked** behind the Black Forest; the rest are open from the start.
- **Greencliff is thinner**: his sticks are drawn at a third of the width.

# limbus-skins 1.4.0

Boss skins: the real boss, not a repaint
- **Matthias, the Roach Emperor and Distorted Hohenheim** (`matthias`, `roach-emperor`,
  `distorted-hohenheim`) are now the bosses themselves: the boss's own body from the game, with
  its own animations and effects (the Roach Emperor's own swarm), and the boss's **whole
  moveset** under its own names - all nine of the Roach Emperor's attacks, Matthias's five and
  his counter, Hohenheim's four - his guard or counter as the defence. Before, the identity was
  repainted with the boss's drawings: Matthias kept Outis's moves, the Roach Emperor was drawn
  at half size to fit, and his boss skills ran past their animation. Needs the Limbus Private
  Server's client patch, **build 6** or later (`run` refuses an older package's).
- `limbus-skins boss on <repaint of a boss>` now does the same: the boss's own body and skills.

Allies, kits and ID cards
- **Allies** (`dulcinea-id`, `distorted-kim-id`): bosses as identities of their own, equipped
  like any other - Dulcinea in Rodion's slot and the eroded Kim Satgat in Meursault's, each with
  the boss's own body and whole moveset. Built only while the private server grants them.
- **Kits** (`roland`): an identity's moves and text only, for a body mod's character - the Black
  Silence's pages on Index Father Yi Sang (Durandal, Ranga Workshop, Crystal Atelier, Old Boys
  Workshop; Furioso on the nine-coin skill), names and dice read from your Library of Ruina.
- **ID cards** (`"card"`): Kali, the three boss skins, the allies and Vergilius redraw their
  identity's card - full art, uptie art, info, profile and support portraits - from card art you
  put in `<data dir>/art/cards/<name>/`. Off with the skin. The game may still play an uptie card
  that is a Spine animation in its detail screen.
- **The Black Forest** also keeps Vergilius (`vergilius-id`, `vergilius-wildhunt`), the Queen of
  Hatred, Solemn Lament's butterfly and Roland's pages until Greencliff falls; the private server's
  mod menu lists them (limbus-skins writes `locks.json` for it).
- **Greencliff is an actual stick man**: his head as drawn on bold straight sticks. Before, a third
  of his height stayed as drawn, so most frames kept his shoulders, arms and bat.
- **The audit**: every built skin is checked before it goes in - other bodies in its file left
  alone, nothing of the game's file gone, no frame that drew the body drawing nothing now, every
  skill and passive real and every motion one the body has. A skin with a finding isn't swapped
  in; `limbus-skins audit` checks what is built.

Fixes
- **LCB Gregor, Outis, Sinclair and Meursault wore parts of other skins.** A sinner's early
  identities share one bundle, and a repaint there changed what the others draw too: LCB Gregor
  died as the Roach Emperor, LCB Outis fought as the Old G Corp. Head Manager. A skin now leaves
  everything another body loads as it is, and its own identity gets a private copy of the shared
  death frame. Greencliff no longer greens Heathcliff's other identities' death either.
- **Solemn Lament flashed back to Yi Sang** (`butterfly-yisang`): one frame of S2, S3 and every
  clash (a motion blur) kept his own head. It wears the butterfly too now, blurred to match.
- **Greencliff vanished with `vergilius-wildhunt` on** (from 1.3.3, now released): only his green
  Wild Hunt is skipped, Greencliff stays green.

Update: `pipx install --force ./limbus_skins-1.4.0-py3-none-any.whl` (Linux) or
`py -m pip install --user limbus_skins-1.4.0-py3-none-any.whl` (Windows); skins rebuild on the next run.
Check the download with `sha256sum -c SHA256SUMS`. None of this has been seen in a fight yet:
try boss skins on a spare account first.

# limbus-skins 1.3.3

Fix
- **Greencliff vanished with `vergilius-wildhunt` on** (as with `limbus-skins on all`): both change
  Wild Hunt Heathcliff's body, and the clash skipped Greencliff whole - no green, no stickman.
  His green Wild Hunt is now a part of its own (`built/greencliff.forms`): a clash skips only
  that, and Greencliff stays green and a stickman.
- **Matthias was a mess in fights** (`matthias`): his identity, Outis as the Middle Nursefather
  (11115), draws every move as a stack of parts, and the repaint only redrew whole frames - so
  Outis's own parts flew round Matthias. He now wears the Ring Pointillist Student (11109),
  whose moves are whole pictures, and repaints refuse layered bodies from now on.

Update: download the `.whl`, then `pipx install --force ./limbus_skins-1.3.3-py3-none-any.whl` (Linux)
or `py -m pip install --user limbus_skins-1.3.3-py3-none-any.whl` (Windows).
Check the download with `sha256sum -c SHA256SUMS`.

# limbus-skins 1.3.2

Fixes
- **Boss skills no longer overrun their animation.** A boss coin carried the boss's own hit
  number, so the Roach Emperor's S2 put three coins on a one-hit motion and the game logged an
  error each time (the stalls some boss skins had). Boss coins now play the identity's own hits,
  and extra coins replay its last hit.

Skins
- **Kali** (`kali`) is OP: 250 HP, Speed 6-10, Haste, Protection and Damage Up on her pages,
  hits of 150-340 - and green coins, though her skills stay Wrath.
- **Greencliff** (`greencliff`) too: 200 HP, Speed 6-10, hits of 100-240 (The Greenest Hunt
  180-240 a hit), green coins as before.
- New for skins: `"green_coins": true` makes every coin of the identity green, whatever its sin.

Install: download the `.whl`, then `pip install --user limbus_skins-1.3.2-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only, and none of it has been seen in a fight yet: try it on a spare account.

# limbus-skins 1.3.1

- **Greencliff the stickman** (`greencliff`): below his head he is a stick figure now - arms,
  legs and bat drawn as lines in every pose, the head as it was. His S3 is **The Greenest
  Hunt**: using it turns him into a green Wild Hunt Heathcliff (half HP still does too; needs
  the Limbus Private Server's client patch, build 3).
- **Kali hits for hundreds** (`kali`): every hit of hers lands for 150-340 - Level Slash
  400-475, Upstanding Slash 450-570, Greater Split: Vertical 600-780 - before resistances
  and crits. Her flavor text still quotes Library of Ruina's dice.
- New for skins: "stick" (a stick figure below the head, for tints), a "skill" in "shift" (a
  body change on using a skill), and a text "note" ahead of a skill's own lines.

Install: download the `.whl`, then `pip install --user limbus_skins-1.3.1-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only, and none of it has been seen in a fight yet: try it on a spare account.

# limbus-skins 1.3.0

Skins
- **Greencliff, the best ID ever** (`greencliff`): 130 HP, Speed 5-9, every skill Gluttony and
  Blunt with buffs on use - Haste, Poise (for every ally on S3), Protection, Damage Up - and
  Rupture and Tremor on hit; Acute Studies, Buy Time and Flame Rooster's Death Defiance as
  passives, Gambit as support. Below half HP he turns into Wild Hunt Heathcliff's body, tinted
  green with him (needs the Limbus Private Server's client patch). While he is on, Wild Hunt
  Heathcliff is green too. He and `vergilius-wildhunt` both change Wild Hunt's body: with both
  on, the one turned on first wins and the other is skipped.
- **Kali** (`kali`) fights with her own Library of Ruina pages: Level Slash (Slash 5-9, 3 Bleed
  next turn on each hit), Upstanding Slash (Blunt 6-10), Greater Split: Vertical (Slash 20-39)
  and Retaliate (counter, Slash 4-8), all Wrath. Their names, and their Ruina cost, dice and
  abilities as flavor text, are read from your own Library of Ruina when she is built.
- **The Roach Emperor** (`roach-emperor`) stands in a swarm of 22 roaches; 10 follow him into
  every move.

New in the tool
- **Skill text:** a rewritten skill gets its own name and description - what it does now, in
  the game's own wording - shown through the Limbus Private Server's client patch.
- **`run` sets a package's client patch** for the session (skill text, green coins, Greencliff's
  Wild Hunt) and clears it when the game exits; `limbus-skins client [--off] <folder>` by hand.
- Skins can now have several effects on a skill, effects on chosen coins, stats (HP and Speed),
  and a body to turn into at low HP. Rewritten skills keep their animation's timing.

Install: download the `.whl`, then `pip install --user limbus_skins-1.3.0-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only. These change how fights play, and none of it has been seen in a fight
yet: try it on a spare account, and if something looks wrong or a fight freezes,
`limbus-skins off <skin>` and open an issue.

# limbus-skins 1.2.0

New skins
- **Greencliff** (`greencliff`): LCB Heathcliff, green from head to toe. A new kind of skin, a
  tint: the identity keeps its own drawings, recolored, so every frame stays in place. Every
  skill of his is Gluttony, the green sin. (On the private server, his death opens the Black Forest.)
- **Kali, the Red Mist** (`kali`), from Library of Ruina, if you own it: her Ruina poses, three to
  a skill, with her chibi head brought down to size; her Ruina pages as skills (Bleed, Poise,
  Blunt, and Greater Split: Vertical as S3); Sancho's blood mist around her; every skill
  Gluttony. Locked until Greencliff dies in a fight on the private server.
- **The Roach Emperor** (`roach-emperor`) on G Corp. Gregor, with his own flies buzzing round
  him; every skill Gluttony.
- **The Middle Nursefather, Matthias** (`matthias`) on the House of Spiders Outis.
- **Distorted Hohenheim** (`distorted-hohenheim`) on Heishou Si Gregor.
- **Vergilius**: `vergilius` (helper: his own kit and battle script) and `vergilius-wildhunt`
  (Wild Hunt as Vergilius on foot, E.G.O. Vergilius mounted).
- **Ahab** is drawn in her regular form now, not the GasHarpoon awakening.

New in the tool
- **Boss skills:** `limbus-skins boss on <skin>` gives a repaint its boss's skills too - power,
  coins and effects, played with the identity's own (repainted) animations. Off by default.
- **Auras:** effect sprites from a boss's own bundle drift around the body in every frame and,
  as moving parts, in a borrowed idle.
- **Library of Ruina sources**, sized by the body rather than the weapon.
- **Locked skins** wait for an unlock the private server grants.
- `sins` recolors any skill, all of them or the defense, on top of boss skills.

Fixes
- **Helper skins no longer freeze fights.** A helper brought its whole story kit, finishers
  included: A Certain Sinclair's S6 waits for a story event that never comes outside its fight,
  Alyssa's last skill kills her, the Queen of Hatred retreats. Those skills are left out now,
  as are ones needing the helper's own battle script; boss skills skip them too. A helper
  without a defense skill keeps the identity's own.
- `preview` shows only the skin's own body. Built skins rebuild themselves on the next `apply`.

Install: download the `.whl`, then `pip install --user limbus_skins-1.2.0-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only. Boss skills change how fights play, and none of the new skins has been
seen in a fight yet: try them on a spare account, and if one looks wrong or a fight freezes,
`limbus-skins off <skin>` and open an issue.
