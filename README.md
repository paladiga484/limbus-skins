# limbus-skins

Dress Limbus Company identities as the characters they mirror, on a **private server**.

- **Repaints:** the identity keeps its own body, moves, skills and upties, and is redrawn as
  another character. Pequod Captain Ishmael fights as GasHarpoon Ahab, Spicebush Yi Sang as
  the bloomed Dongbaek, Solemn Lament Yi Sang wears the butterfly head of the Funeral of the
  Dead Butterflies, and so on.
- **Helper skins:** the identity takes a story helper unit's body *and* its skills: LCB
  Ryōshū as Arayashiki Ryōshū from stage 9-49, LCB Sinclair as A Certain Sinclair, Love and
  Hate Don Quixote as the Walpurgis Night Queen of Hatred.

Everything is built on your machine from the game files you already have. This repository
contains no game art, and the tool never connects to the internet.

> **Private servers only.** Helper skins change skills. Any modified game file on the
> official servers breaks the terms of service and can get your account banned. The tool asks
> you to confirm this once before it changes anything.

## Install

You need the game installed through Steam (Windows, or Linux with Proton) and launched at least
once, so its download cache exists, plus Python 3.9 or newer.

Download `limbus_skins-1.0.1-py3-none-any.whl` from the release, then:

```
pip install --user limbus_skins-1.0.1-py3-none-any.whl     # or: pipx install <the .whl>
limbus-skins doctor
```

If your shell can't find `limbus-skins` afterwards (common on Windows), every command also
works as `python -m limbus_skins ...` (`py -m limbus_skins ...` on Windows).

`doctor` finds the game and its cache and checks the Python packages. If it can't find them:

```
limbus-skins config --game "<Steam>/steamapps/common/Limbus Company/LimbusCompany_Data" --cache "<cache folder>"
```

The cache is `%USERPROFILE%\AppData\LocalLow\Unity\ProjectMoon_LimbusCompany` on Windows. On
Linux it's the same path inside the Proton prefix:
`<Steam library>/steamapps/compatdata/1973530/pfx/drive_c/users/steamuser/AppData/LocalLow/Unity/ProjectMoon_LimbusCompany`.

## Use

```
limbus-skins list                      # every skin, by sinner
limbus-skins on ahab arayashiki-ryoshu # by name; ids (10808), sinner names, `helpers`, `repaints`, `tested` and `all` work too
limbus-skins off ishmael
limbus-skins run --yes -- <the command that starts the game on your private server>
```

`run` builds whatever is new or out of date, swaps the skins in, starts your launcher, and
puts the original files back when the game exits. Your choices are remembered, so after the
first time `limbus-skins run -- <launcher>` is all you need, including after game updates.

To do it by hand: `limbus-skins apply`, start the game, then `limbus-skins restore` after
closing it (or `limbus-skins watch`, which restores as soon as the game exits).

`limbus-skins preview ahab ahab.png` renders a repaint's frames to a picture before you play,
and its standing idle to an animated `ahab-idle.gif`.
`limbus-skins sheet` draws every skin that is on, standing idle, before and after, into one picture.

Head skins take your own picture too: `limbus-skins art butterfly-yisang butterfly.png --invert`
cuts the picture out of a dark background (or uses its transparency), `--invert` swaps light and
dark, and the next `apply` paints it on. `limbus-skins art butterfly-yisang --reset` goes back to
the in-game art. The picture stays on your computer. The butterfly head also glows softly,
and in its idle the wings flutter and a few pale motes drift up.

Skins only show in battle. Menus, portraits and identity art stay as they are.

## The skins

| Skin | Identity | Kind | Becomes | Seen in a fight |
|---|---|---|---|---|
| `dongbaek-spicebush` | Yi Sang 10104 | repaint | Dongbaek (bloomed) | not yet |
| `starbuck` | Yi Sang 10107 | repaint | Starbuck | not yet |
| `butterfly-yisang` | Yi Sang 10110 | repaint | the Funeral of the Dead Butterflies' head | not yet |
| `rien` | Yi Sang 10115 | repaint | Rien | not yet |
| `kromer` | Faust 10204 | repaint | Kromer | not yet |
| `sasha` | Faust 10210 | repaint | Sasha | not yet |
| `alyssa` | Faust 10211 | helper | Alyssa | not yet |
| `albina` | Faust 10215 | repaint | Albina | not yet |
| `queen-of-hatred` | Don Quixote 10312 | helper | Queen of Hatred (Walpurgis Night 6) | yes |
| `sora` | Don Quixote 10314 | repaint | Sora | not yet |
| `arayashiki-ryoshu` | Ryōshū 10401 | helper | Arayashiki Ryōshū (stage 9-49) | yes |
| `nelly` | Ryōshū 10408 | repaint | Nelly | not yet |
| `lin-daiyu` | Ryōshū 10413 | helper | Lin Daiyu | not yet |
| `pinky-nursefather` | Ryōshū 10415 | repaint | the Pinky Nursefather | not yet |
| `guido` | Meursault 10504 | repaint | Guido | not yet |
| `bamboo-hatted-kim` | Meursault 10508 | helper | Bamboo-hatted Kim | not yet |
| `dead-rabbits-boss` | Meursault 10509 | repaint | the old Dead Rabbits Boss | not yet |
| `camille` | Meursault 10511 | repaint | Camille | not yet |
| `lei-heng` | Meursault 10512 | repaint | Lei Heng | not yet |
| `cassetti` | Meursault 10513 | repaint | Cassetti | not yet |
| `jia-qiu` | Hong Lu 10613 | repaint | Jia Qiu | not yet |
| `callisto` | Hong Lu 10614 | repaint | Callisto | not yet |
| `dongbaek-sunshower` | Heathcliff 10705 | repaint | Dongbaek (Sunshower) | not yet |
| `queequeg` | Heathcliff 10707 | repaint | Queequeg | not yet |
| `lucio` | Heathcliff 10716 | repaint | Lucio | not yet |
| `ahab` | Ishmael 10808 | repaint | GasHarpoon Ahab | not yet |
| `jun-ishmael` | Ishmael 10811 | repaint | Jun | not yet |
| `jia-xichun` | Ishmael 10812 | helper | Jia Xichun | not yet |
| `kira` | Ishmael 10814 | helper | Kira | not yet |
| `aeng-du` | Ishmael 10815 | helper | Aeng-du | not yet |
| `linamovible` | Ishmael 10816 | repaint | L'Inamovible | not yet |
| `niko` | Rodion 10905 | repaint | Niko | not yet |
| `dulcinea` | Rodion 10911 | repaint | Dulcinea | not yet |
| `rufo` | Rodion 10915 | repaint | Rufo | not yet |
| `valencina` | Rodion 10916 | repaint | Valencina | not yet |
| `certain-sinclair` | Sinclair 11001 | helper | A Certain Sinclair (Canto IX) | yes |
| `aida` | Sinclair 11004 | repaint | Aida | not yet |
| `werner` | Sinclair 11012 | repaint | Werner | not yet |
| `pwie` | Sinclair 11016 | helper | Pwie, the Crimson God | not yet |
| `old-gcorp-head` | Outis 11103 | repaint | the Old G Corp. Head Manager | not yet |
| `josephine` | Outis 11108 | repaint | Josephine | not yet |
| `barber` | Outis 11111 | repaint | the Barber (bloodfiend) | not yet |
| `jun-gregor` | Gregor 11208 | repaint | Jun | not yet |
| `priest` | Gregor 11210 | repaint | the Priest | not yet |
| `hohenheim` | Gregor 11214 | helper | Hohenheim | not yet |

"Not yet" means it builds and its frames check out, but nobody has watched it in a fight yet.
If one looks wrong or a fight freezes, turn it off with `limbus-skins off <skin>` and open an issue.

## How it works

- The game reads battle bodies and its static data from Unity's download cache. A session
  copies the built files over the cached ones and keeps the originals aside. `restore` puts
  them back byte for byte. Nothing is changed while the game is running.
- **Repaints** rewrite only an identity's own body bundle. Each frame is pointed at the
  matching drawing of the other character (idle to idle, guard to guard, each skill's frames
  spread across theirs), packed into the body's own atlas pages. Only the objects the
  identity's own body loads are touched (read from the bundle's preload table), so bundles
  that hold several characters keep the others as they are, and only the character's own
  animator lends frames (not a summon or sidekick drawn alongside it). The identity's other
  motions (reloads, parries...) take the other character's motion of the same name, else
  its first skill; `build` says which motions the other character has no drawing for.
- Most identities stand idle as a **Spine skeleton** and only use the frames to move and
  fight. A repaint hides the skeleton's parts and adds one part that shows the new idle
  frames, placed where the frames stand; bones, skins, constraints and animation names stay,
  so the game finds everything it looks for. A head skin swaps only the skeleton's head part.
- **Helper skins** rewrite the identity's record in the static-data bundle: the helper's body,
  its skills, and its defence skill.
- Since version 1.117.1 the game checks each cached file's CRC and re-downloads any that
  differ. Each built file is given the original's CRC (4 spare bytes after the last object),
  so the game keeps it.
- After a game update a skin's target file changes. `run`/`apply` notice and rebuild it.

## Limits

- Only tested by the author on Linux (Proton). Windows support is written but untested.
- Some identities' bodies are Spine skeletons rather than frame drawings (Middle Big Brother
  Heathcliff, Pinky Apprentice Sinclair), so they can't be repainted.
- A repainted idle stands still (or flips through the other character's idle frames); the
  breathing of the original skeleton is gone. Head skins keep it.
- `sheet` and `preview` draw skeletons without their IK constraints, so some "before" pictures
  look bent. The game draws them properly.
- An identity can wear one skin at a time. Skins that share a bundle can't be on together; `apply` says which one it skipped.
- Swapping an identity onto another character's body without that character's own skills
  crashes battles, and boss units' skills only work in boss fights. That's why helper skins
  are limited to the game's helper units.

## Undo everything

`limbus-skins restore` with the game closed, then `pip uninstall limbus-skins`. If anything
ever goes wrong, Steam's *Verify integrity of game files* puts the game back too.
Its own files live in `~/.config/limbus-skins` and `~/.local/share/limbus-skins` (Linux) or
`%APPDATA%\limbus-skins` and `%LOCALAPPDATA%\limbus-skins` (Windows).

## Credits

Limbus Company and all of its art belong to Project Moon. This is a fan project and is not
affiliated with or endorsed by Project Moon. Reading and writing Unity files uses
[UnityPy](https://github.com/K0lb3/UnityPy). Character counterparts follow the
[Limbus Company Wiki](https://limbuscompany.wiki.gg).

MIT licence.
