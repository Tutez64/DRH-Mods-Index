# DRH Mods Index

The mods [DRH Launcher](https://github.com/Tutez64/DRH-Launcher) offers for [Dungeon Rampage Haxe](https://github.com/Tutez64/Dungeon-Rampage-Haxe). It points to each version's zip, a release asset of the mod's own repository; it holds no mod.

## Submitting a version

One file per version, `mods/<id>/<version>.json`. `--pack-mod` writes it with the release, `--submit-mod` opens the pull request. The CI checks it with the latest launcher (`--check-entry`) and writes in its summary the diff of the sources against the previous version, which the reviewer reads. A version is withdrawn by a pull request that only adds `yanked` (why) to its file. An `id` stays with its repository: moving it to another one needs the reviewer's `takeover` label. The rules are in [Index entry](https://github.com/Tutez64/Dungeon-Rampage-Haxe/blob/master/docs/modding.md#index-entry); how to make a mod, in [Making a mod](https://github.com/Tutez64/Dungeon-Rampage-Haxe/blob/master/docs/making-a-mod.md).

## Reading the index

Every entry, generated on each merge: `https://tutez64.github.io/DRH-Mods-Index/index.json`. Its format is in [Index entry](https://github.com/Tutez64/Dungeon-Rampage-Haxe/blob/master/docs/modding.md#index-entry).

## License

The entries and `index.json` are under [CC BY 4.0](LICENSE): reuse them, crediting the DRH Mods Index and, for its texts, each entry's `author`. Submitting an entry publishes it under that license, its texts included. The CI's code, under `.github/`, is under [GPLv3](.github/LICENSE).
