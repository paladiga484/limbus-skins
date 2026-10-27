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
