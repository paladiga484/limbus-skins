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
