# limbus-skins

New characters, skins and boss fights for Limbus Company, for use on a **private server**.

- **New identities:** characters from Library of Ruina (the Sephirah, Binah, Roland, Argalia,
  Xiao and more), Canto X's bosses, and other bosses as allies. Each has its own body and skills.
- **Skins:** turn an identity into another character, like Captain Ahab or Greencliff.
- **Boss fights** in EXP Luxcavation: Nothing There, WhiteNight, and the Apocalypse Bird.

It builds everything from your own copy of the game and never goes online.

> Use it only on a private server. Changed game files on the official servers can get you banned.

## Install

Easiest: install the [Limbus Private Server](https://github.com/paladiga484/myosotis-server/releases),
which includes this.

By hand (needs Python 3.9+ and Limbus started once):

```
pipx install ./limbus_skins-<version>-py3-none-any.whl
limbus-skins doctor
```

## Use

```
limbus-skins list            # see everything
limbus-skins on all          # turn everything on (or: on ahab)
limbus-skins off ahab        # turn one off
limbus-skins run -- <game launcher>
```

Something looks wrong? Turn it off and open an issue.

## Remove

Close the game, run `limbus-skins restore`, then `pipx uninstall limbus-skins`.

## Credits

Fan project, not made or approved by Project Moon. The characters, poses, cards and pictures
are Project Moon's (Limbus Company, Library of Ruina, Lobotomy Corporation). The copies that
come with this tool are in `limbus_skins/data/`; some card art came through the
[Library of Ruina Wiki](https://libraryofruina.wiki.gg) and the
[Limbus Company Wiki](https://limbuscompany.wiki.gg).

Uses [UnityPy](https://github.com/K0lb3/UnityPy) and [Pillow](https://python-pillow.github.io).
The code is MIT licensed; Project Moon's art is not.
