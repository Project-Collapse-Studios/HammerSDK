"""Handles user configuration common to the different scripts."""
from typing import Final, Literal, Self
from collections.abc import Callable, Iterator
from pathlib import Path
import importlib.resources
import fnmatch
import hashlib
import re
import struct
import sys

from srctools import AtomicWriter, Keyvalues, conv_int, logger, NoKeyError
from srctools.dmx import Element
from srctools.filesys import FileSystem, FileSystemChain, RawFileSystem, VPKFileSystem
from srctools.game import Game
from srctools.steam import find_app
import attrs

from .plugin import BUILTIN as BUILTIN_PLUGIN, PluginFinder, Source as PluginSource
from .props_config import Opt, Options


__all__ = [
    "Expander", "Config", "parse",

    # Options
    "VERSION", "GAMEINFO", "AUTO_PACK", "PACK_VPK", "PACK_DUMP", "PACK_STRIP_CUBEMAPS",
    "PACK_ALLOWLIST", "PACK_BLOCKLIST", "SEARCHPATHS", "SOUNDSCRIPT_MANIFEST",
    "PARTICLES_MANIFEST", "MODEL_COMPILE_DUMP",
    "PROPCOMBINE_QC_FOLDER", "PROPCOMBINE_CROWBAR", "PROPCOMBINE_CACHE",
    "PROPCOMBINE_VOLUME_TOLERANCE", "PROPCOMBINE_MIN_AUTO_RANGE", "PROPCOMBINE_MAX_AUTO_RANGE",
    "PROPCOMBINE_MIN_CLUSTER", "PROPCOMBINE_MIN_CLUSTER_AUTO", "PROPCOMBINE_BLACKLIST",
    "PROPCOMBINE_PACK", "PLUGINS", "TRANSFORM_OPTS", "DISABLED_TRANSFORMS",
]


LOGGER = logger.get_logger(__name__)
MAIN_VERSION: Final = 1  # Ordinal version for the core configs.
MAIN_SECTION_NAME: Final = 'postcompiler'  # Name for the core config.

MAIN_CONF_NAME: Final = 'hammeraddons.vdf'
MAIN_CONF_UPDATE_NAME = 'hammeraddons.new.vdf'
PATHS_CONF_NAME: Final = 'hammeraddons_paths.vdf'
GAMES_CONF_NAME: Final = 'hammeraddons_game.dmx'

CONF_OLD_NAME: Final = 'srctools.vdf'
PATHS_OLD_NAME: Final = 'srctools_paths.vdf'

PATH_KEY_GAME: Final = 'gameinfo_path'
PATH_KEY_MAP: Final = 'mapdir_path'


PREDEFINED_PATHS = {PATH_KEY_GAME, PATH_KEY_MAP}

# Matches cubemap files. Put here, so we can write it into the docstring.
CUBEMAP_REGEX = r"materials/maps/.*/(c[0-9-]+_[0-9-]+_[0-9-]+|cubemapdefault)(\.hdr)?\.vtf"

# Tags we use in our engine dump.
USED_PACK_TAGS: set[str] = {
    'hl1', 'hl2', 'episodic',
    'tf2',
    'mapbase', 'entropyzero2',
    'mesa',
    'p2', 'strata',
}

PATHS_CONF_STARTER: Final = f'''\
// This config contains a list of directories which can be referenced by the main config.
// Keeping this a separate file allows the main config to be shared in a mod team, while this
// config is customised for each user's installation locations.
// The keys here are then referenced by specifying "|key|" at the start of a path.
// If no root is specified, paths are relative to these configs.
// Some names are predefined: |{PATH_KEY_GAME}| and |{PATH_KEY_MAP}|.
"Paths"
    {{
    // For example this makes "|hl2|/episodic/ep1_pak_dir.vpk" valid in searchpaths.
    // "hl2" "C:/Program Files/Steam/SteamApps/common/Half Life 2/"
    }}
'''
# Special 'paths' used to detect specific expansion scenarios for the game folder.
GAMEINFO_RECURSION_KEY: Final = '::recursion::'  # Expanding for the game folder itself.
GAMEINFO_MISSING_KEY: Final = '::missing::'  # Expanding for plugins, when the game is missing.

# A function taking a configured path, and expanding |refs| to get the full location.
type Expander = Callable[[str], Path]
type ExpanderRoots = dict[str, Path | Literal['::recursion::', '::missing::']]


def make_expander(roots: ExpanderRoots, orig_root: Path) -> Expander:
    """Produce a function that expands configs potentially containing || refs."""
    appid_cache: dict[int, Path] = {}

    def expander(path: str) -> Path:
        """Expand a reference potentially containing || refs."""
        root: Path = orig_root
        orig_path = path
        appid = -1

        if path.startswith('|'):
            try:
                _, ref, path = path.split('|', 2)
            except ValueError:
                LOGGER.warning('Invalid |ref| path prefix in {!r}', path)
            else:
                path = path.lstrip('\\/')  # Make |loc|/blah/ allowed, don't treat as a root.
                try:
                    found_root = roots[ref.casefold()]
                except KeyError:
                    LOGGER.warning(
                        '|{}| is not defined in {}! Assuming {}\nKnown: {}',
                        ref, PATHS_CONF_NAME, root,
                        ', '.join(sorted(roots)),
                    )
                else:
                    # Two special cases, detected by specific constants being set. Use identity
                    # compare, we're putting the exact values in, users should never set these.
                    if found_root == GAMEINFO_RECURSION_KEY:
                        # We're trying to expand the game key to find gameinfo itself.
                        # That's an infinite loop.
                        raise ValueError(
                            f'Cannot use |{PATH_KEY_GAME}| to locate the '
                            'game folder, this is an infinite loop.'
                        )
                    elif found_root == GAMEINFO_MISSING_KEY:
                        # Using game loc in a plugin, but game dir isn't set. We're only doing
                        # this to create/update the config file, this would only happen if the user
                        # messed up.
                        LOGGER.warning(
                            '|{}| used in plugin filenames, but no game folder provided! '
                            'Plugins will not load correctly!',
                            PATH_KEY_GAME,
                        )
                    else:  # All good.
                        root = found_root
        # Game mount, we just replace the <appid> with a path.
        elif path.startswith("<") and (end := path.find(">")) != -1:
            appid = conv_int(path[1:end], -1)
            path = path[end+1:].lstrip('\\/')  # Same as above.

        if appid != -1:
            try:
                root = appid_cache[appid]
            except KeyError:
                LOGGER.info("Mounting appid {}", appid)
                try:
                    info = find_app(appid)
                except KeyError:
                    LOGGER.warning("No game with appid {} found!", appid)
                else:
                    appid_cache[appid] = root = info.path
                    LOGGER.info(f"Mounted game {info.name} with path: {root}")

        expanded = Path(root, path).resolve()
        LOGGER.debug('Expanding {} -> {}', orig_path, expanded)
        return expanded
    return expander


@attrs.frozen(kw_only=True)
class GameConfig:
    """Special options defining the behaviour of the game itself.

    Normally picked from shipped presets, only needs to be customised by mod devs.
    """
    # aliases: A list of additional names which can be used to refer to this game branch.

    # The Steam ID for this branch, if unique. This allows gameinfo to be used to identify the branch.
    # TODO: Good idea?
    # steamid: str

    # List of names which identify certain game branches. This is mainly used to tweak what
    # resources specific entities require.
    # See `USED_PACK_TAGS` further above for the possible values.
    tags: frozenset[str]

    # Before L4D, entity I/O used ',' to separate the different parts in VMFs/BSPs.
    # After L4D, 0x1B is used. If the map already has I/O we can detect the correct value,
    # but if not this is necessary to produce the right result.
    io_comma_sep: bool

    # All games support instances, but only post-L4D support instance I/O.
    instance_proxies: bool

    # Whether the game supports VScript. A bunch of features are turned off if not.
    vscript: bool
    # If set, the game has an alternate character to use in RunScriptCode, so quotes can be used.
    # MapBase uses double '', while TF2 uses `.
    vscript_quote: str

    # If set, the filename to use for packed particles manifest, where "<map name>" is replaced.
    # Examples:
    # * particles/particles_manifest.txt
    # * maps/<map name>_particles.txt (TF2, Portal 2)
    # * particles/<map name>_manifest.txt (L4D2)
    particles_manifest: str

    # Location of StudioMDL, relative to the game root.
    studiomdl_path: str

    @classmethod
    def parse(cls, root: Element) -> Self:
        """Parse from a DMX element."""
        # Only advanced users should need to change this, tracebacks are fine.
        return cls(
            tags=frozenset({tag.casefold() for tag in root['tags'].iter_string()}),
            io_comma_sep=root['io_comma_sep'].val_bool,
            instance_proxies=root['instance_proxies'].val_bool,
            vscript=root['vscript'].val_bool,
            vscript_quote=root['vscript_quote'].val_str,
            particles_manifest=root['particles_manifest'].val_str,
            studiomdl_path=root['studiomdl_path'].val_str,
        )


@attrs.frozen(kw_only=True)
class Config:
    """Result of parse()."""
    opts: Options
    game: Game
    fsys: FileSystemChain
    pack_blacklist: set[FileSystem]
    plugins: PluginFinder
    expand_path: Expander
    plugin_conf: dict[str, Options]

    @property
    def loc(self) -> Path:
        """Location of the configs."""
        path = self.opts.path
        assert path is not None
        return path


def find_conf(map_path: Path) -> tuple[Path, Keyvalues]:
    """From some directory, locate the config files.

    The first hammeraddons.vdf file found in a parent directory is parsed.
    If none can be found, it tries to find the first subfolder of 'common/' and
    writes a default copy there. FileNotFoundError is raised if none can be
    found.
    """

    # If the path is a folder, add a dummy folder so parents yields it.
    # That way we check for a config in this folder.
    if not map_path.suffix:
        map_path /= 'unused'

    for folder in map_path.parents:
        conf_path = folder / MAIN_CONF_NAME
        if not conf_path.exists():
            conf_path = folder / CONF_OLD_NAME
        if conf_path.exists():
            LOGGER.info('Config path: "{}"', conf_path.absolute())
            with open(conf_path, encoding='utf8') as f:
                kv = Keyvalues.parse(f, conf_path)
            return conf_path, kv

    LOGGER.warning('Cannot find a valid config file!')

    # Try to find the game root to place the config at.
    # We look for the steam library folder or sourcemods folder above.
    for folder in map_path.parents:
        if folder.parent.stem in ('common', 'sourcemods'):
            break
    else:
        # Give up, put next to the input path.
        folder = map_path.parent
    conf_path = folder / MAIN_CONF_NAME

    LOGGER.warning('Writing default to "{}"', conf_path)
    return conf_path, Keyvalues.root()


def load_paths_config(main_conf: Path) -> ExpanderRoots:
    """Load the hammeraddons_paths.vdf config file."""
    roots: ExpanderRoots = {}
    conf_loc = main_conf.with_name(PATHS_CONF_NAME)
    legacy_loc = main_conf.with_name(PATHS_OLD_NAME)
    for loc in [conf_loc, legacy_loc]:
        LOGGER.info('Paths config: {}', loc)
        try:
            with open(loc, encoding='utf8') as f:
                for kv in Keyvalues.parse(f).find_children('Paths'):
                    if kv.has_children():
                        LOGGER.warning('Paths configs may not be blocks!')
                    else:
                        name = kv.name.strip('|')
                        if name in PREDEFINED_PATHS:
                            LOGGER.warning(
                                '|{}| cannot be defined in the path config - '
                                'the following names are builtin: {}',
                                kv.name, sorted(PREDEFINED_PATHS),
                            )
                        roots[name] = path = Path(kv.value).resolve()
                        LOGGER.info('Path |{}| = {}', name, path)
            break  # Found
        except FileNotFoundError:
            pass
    else:
        LOGGER.info('Writing initial paths config to {}', conf_loc)
        conf_loc.write_text(PATHS_CONF_STARTER, encoding='utf8')
    return roots


def calc_searchpaths(
    opts: Options, game: Game, expand_path: Expander,
) -> tuple[FileSystemChain, set[FileSystem]]:
    """Apply the searchpaths option to the loaded filesystem."""
    fsys_chain = game.get_filesystem()
    blacklist: set[FileSystem] = set()

    if not opts.get(PACK_VPK):
        for fsys, prefix in fsys_chain.systems:
            if isinstance(fsys, VPKFileSystem):
                blacklist.add(fsys)

    # Extract to apply after, so order does not matter.
    blacklist_names = set()

    for kv in opts.get(SEARCHPATHS):
        if kv.has_children():
            raise ValueError('Config "searchpaths" value cannot have children.')
        assert isinstance(kv.value, str)
        if kv.name == 'nopack':
            search = expand_path(kv.value).as_posix()
            # Put this back on the end, so we match folders.
            if kv.value.endswith(('/', '\\')):
                search += '/'
            blacklist_names.add(search)
            continue

        if kv.value.endswith('.vpk'):
            fsys = VPKFileSystem(str(expand_path(kv.value)))
        else:
            fsys = RawFileSystem(str(expand_path(kv.value)))

        if kv.name in ('prefix', 'priority'):
            LOGGER.debug('Added priority searchpath {}', fsys)
            fsys_chain.add_sys(fsys, priority=True)
        elif kv.name in ('path', 'pack'):
            LOGGER.debug('Added searchpath {}', fsys)
            fsys_chain.add_sys(fsys)
        else:
            raise ValueError(f'Unknown searchpath key "{kv.real_name}"!')

    for search in blacklist_names:
        LOGGER.debug('Disabling packing for "{}"...', search)
        for fsys, prefix in fsys_chain.systems:
            # Treat folders as ending with a slash, but not VPKs.
            targ_path = Path(fsys.path).as_posix()
            if isinstance(fsys, RawFileSystem):
                targ_path += '/'
            if fnmatch.fnmatch(targ_path, search):
                LOGGER.debug('- Disabled {}', fsys)
                blacklist.add(fsys)
    return fsys_chain, blacklist


def parse_games_conf(opts: Options, game_folder: Path) -> GameConfig:
    """Locate the game configuration to use.

    This can either use hammeraddons_game.vdf next to gameinfo, or lookup from an inbuilt file.
    """
    try:
        with open(game_folder / GAMES_CONF_NAME, 'rb') as f:
            LOGGER.info('Parsing game config {}', f.name)
            mod_conf, fmt_name, fmt_ver = Element.parse(f, unicode=True)
    except FileNotFoundError:
        pass
    else:
        if fmt_name.casefold() != 'hagame' or fmt_ver != 1:
            raise ValueError(
                f'Invalid {GAMES_CONF_NAME} file. '
                f'Expected a HAGame v1 format, '
                f'got "{fmt_name}" version {fmt_ver}!'
            )
        if mod_conf.type.casefold() == 'gameslist':
            # Allow a redundant list, if only one game is present.
            names = mod_conf.keys() - {'name'}
            if len(names) != 1:
                raise ValueError(
                    f'Invalid {GAMES_CONF_NAME} file. List should have exactly 1 game defined, '
                    f'or have it as a root element.'
                )
            [name] = names
            return GameConfig.parse(mod_conf[name].val_elem)
        else:
            return GameConfig.parse(mod_conf)
    # No override file, load our own.
    package = importlib.resources.files('hammeraddons')
    with (package / 'games.dmx').open('rb') as f:
        LOGGER.debug('Parsing inbuilt games configs')
        games_list, fmt_name, fmt_ver = Element.parse(f, unicode=True)
    assert fmt_name.casefold() == 'hagame' and fmt_ver == 1, (fmt_name, fmt_ver)

    selected = opts.get(GAME_BRANCH).casefold()

    # Go through all games, collecting the names while checking each. We only need to
    # parse one.
    name_report: list[tuple[str, list[str]]] = []
    for name, elem_attr in games_list.items():
        if name == 'name':
            continue
        elem = elem_attr.val_elem
        if selected == name or any(
            selected == alias.casefold() for alias in elem['aliases'].iter_string()
        ):
            LOGGER.info('Selected game: {}', name)
            return GameConfig.parse(elem)
        name_report.append((elem.name, list(elem['aliases'].iter_string())))

    raise ValueError(
        f'No game configuration defined. Set "game" in hammeraddons.vdf to a valid game,'
        f' or define your own hammeraddons_game.vdf if this is a custom mod. Valid games:\n' +
        '\n'.join(
            f'- {name} ({", ".join(aliases)})' if aliases else f'- {name}'
            for name, aliases in name_report
        )
    )


def parse_plugins(opts: Options, expand_path: Expander) -> PluginFinder:
    """Parse and locate all plugins."""
    sources: dict[str, PluginSource] = {}

    if hasattr(sys, 'frozen'):
        builtin_transforms = (Path(sys.executable).parent / 'transforms').resolve()
    else:
        # Assume working directory is HammerAddons.
        builtin_transforms = Path('transforms').resolve()

    # Find all the plugins and make plugin objects out of them
    unnamed_ind = 1
    for kv in opts.get(PLUGINS):
        source = PluginSource.parse(kv, expand_path)
        if not source.id:
            source.id = f'unnamed_{unnamed_ind}'
            unnamed_ind += 1
        if source.id in sources:
            raise ValueError(f'Plugin "{source.id}" declared twice!')
        sources[source.id] = source

    if BUILTIN_PLUGIN not in sources:
        sources[BUILTIN_PLUGIN] = PluginSource(BUILTIN_PLUGIN, builtin_transforms, recursive=True)

    for source in sources.values():
        LOGGER.debug('- {!r}', source)

    plugin_finder = PluginFinder('hammeraddons.plugins', sources)
    sys.meta_path.append(plugin_finder)
    return plugin_finder


def parse_plugin_confs(plugins: PluginFinder, kv: Keyvalues) -> dict[str, Options]:
    """Parse configs for each plugin."""
    confs = {}
    for module in plugins.modules.values():
        if not hasattr(module, 'CONFIG'):
            continue
        conf = module.CONFIG
        if not isinstance(conf, Options):
            LOGGER.warning('Non props-config CONFIG found for plugin "{}"', module.__name__)
            continue
        LOGGER.info('Loading config for plugin "{}" under key "{}"', module.__name__, conf.name)
        name = conf.name.casefold()
        # Most of these aren't used, but reserve them for if we do.
        if name in {'precompiler', 'packer', 'postcompiler', 'hammeraddons', 'srctools'}:
            raise ValueError(f'Config key "{conf.name}" is reserved for core configuration!')
        if name in confs:
            raise ValueError(f'Config key "{conf.name}" used twice!')
        conf.load(kv.find_block(name, or_blank=True))
        confs[name] = conf
    return confs


def update_check(conf_path: Path, main: Options, plugins: dict[str, Options]) -> bool:
    """Check if any configuration definitions have changed, and if so begin updating the configs."""
    hasher = hashlib.sha256(usedforsecurity=False)
    main.hash(hasher)
    hasher.update(struct.pack('<I', len(plugins)))
    for plug_id, opt in sorted(plugins.items()):
        hasher.update(plug_id.encode('utf8'))
        opt.hash(hasher)
    runtime_version = hasher.hexdigest()
    updated = new = False
    if not conf_path.exists():
        LOGGER.debug('Version: ', runtime_version)
        LOGGER.info('Writing new config to {}...', conf_path)
        LOGGER.info('Please open this config and set options to match your game.')
        write_path = conf_path
        new = True
    else:
        file_version = main.get(VERSION)
        LOGGER.debug('Expected: {}', runtime_version)
        LOGGER.debug('Current:  {}', file_version)
        if file_version == runtime_version:
            LOGGER.info('Config up to date.')
            return False  # No update required.
        write_path = conf_path.with_name(MAIN_CONF_UPDATE_NAME)
        updated = True
        LOGGER.info('Saving updated config to {}...', write_path)
    main.set_opt(VERSION, runtime_version)
    with AtomicWriter(write_path) as f:
        f.write('// Main Configuration:\n')
        main.save(f, 'Postcompiler')

        if plugins:
            f.write('\n\n// Plugin Configurations:\n')

        for plug_id, opt in sorted(plugins.items()):
            opt.save(f, plug_id)
    if updated:
        LOGGER.warning(
            'Hammeraddons configurations have updated. A new file has been saved as:\n'
            f'{write_path}\n'
            'Compare with your old configuration and update any settings, then save as hammeraddons.vdf.'
        )
    return updated or new


def parse(map_path: Path, game_folder: str | None = '') -> Config:
    """Load the config, plugins, and parse."""
    conf_path, conf_kv = find_conf(map_path)

    LOGGER.info('Loading main config options...')
    opts = Options(MAIN_SECTION_NAME, MAIN_VERSION, globals())
    # "Config" {} is the old location
    try:
        main_kv = conf_kv.find_block(MAIN_SECTION_NAME)
    except NoKeyError:
        # Legacy location.
        main_kv = conf_kv.find_block('config', or_blank=True)
    opts.load(main_kv)
    opts.path = conf_path
    path_roots = load_paths_config(conf_path)
    # We know where this is already.
    path_roots[PATH_KEY_MAP] = map_path.parent

    expand_path = make_expander(path_roots, conf_path.parent)

    if not game_folder:
        game_folder = opts.get(GAMEINFO)

    game: Game | None = None
    pack_blacklist: set[FileSystem] = set()
    fsys: FileSystemChain | None = None

    if game_folder:
        # Marker to ensure gameinfo doesn't try to recurse.
        path_roots[PATH_KEY_GAME] = GAMEINFO_RECURSION_KEY
        game = Game(expand_path(game_folder))
        LOGGER.info('Game folder: {}', game.path)
        # Now we located it, other definitions can use this loc.
        path_roots[PATH_KEY_GAME] = game.path

        fsys, pack_blacklist = calc_searchpaths(opts, game, expand_path)
    else:
        LOGGER.error('No game folder specified.')
        # Chicken and egg problem. We may need the game folder to locate plugins,
        # but need plugins loaded to generate the full config. Continue anyway to ensure the config
        # is up to date. If a user deliberately unset the game folder but set plugins to use them,
        # expand_path() will catch the issue.
        path_roots[PATH_KEY_GAME] = GAMEINFO_MISSING_KEY

    plugins = parse_plugins(opts, expand_path)

    LOGGER.info('Loading plugins...')
    plugins.load_all()

    plugin_conf = parse_plugin_confs(plugins, conf_kv)
    updated = update_check(conf_path, opts, plugin_conf)

    if game is None or fsys is None:
        LOGGER.error(
            'No game folder specified!\n'
            'Add -game $gamedir to the command line, or set it in "{}".',
            conf_path
        )
        sys.exit(2)
    if updated:
        sys.exit(2)

    # Identify which game we have.
    game_conf = parse_games_conf(opts, game.path)

    return Config(
        opts=opts,
        game=game,
        fsys=fsys,
        pack_blacklist=pack_blacklist,
        plugins=plugins,
        expand_path=expand_path,
        plugin_conf=plugin_conf,
    )


def packfile_filters(block: Keyvalues, kind: str) -> Iterator[re.Pattern[str]]:
    """Convert an allowlist/blocklist block into a bunch of regexes."""
    for kv in block:
        if kv.has_children():
            raise ValueError('A keyvalue sub-block is not valid inside the {} filter block!')
        if kv.name in ('path', 'file', 'folder'):
            yield re.compile(re.escape(kv.value.replace('\\', '/')))
        elif kv.name == 'glob':
            # Ensure it matches at the start of the string only.
            yield re.compile('^' + fnmatch.translate(kv.value))
        elif kv.name in ('re', 'regex', 'pattern'):
            yield re.compile(kv.value)
        else:
            raise ValueError(f'Invalid filter type "{kv.real_name}" for {kind}!')


# Specially handled above.
VERSION = Opt.string(
    'version', '',
    """A unique ID to identify the config version. 
    If available options change, a new copy of the config is saved as hammeraddons.new.vdf. Copy over
    any changes to that file, then overwrite the original config."""
)


GAMEINFO = Opt.string_or_none(
    'gameinfo',
    """The main game folder. portal2/ for Portal 2, episodic/ for Episode 1, etc.
    This is relative to the config file.
    """,
)

GAME_BRANCH = Opt.string(
    'game', "",
    """The name of the game/mod, used to idenfity various game behaviours like VScript support.
    If a hammeraddons_game.dmx file is found next to gameinfo.txt, that is used instead of this.
    """
)

AUTO_PACK = Opt.boolean(
    'auto_pack', True,
    """Automatically find and pack files in the map. 
    If this is disabled, specifically-indicated files will still be 
    added as well as their dependencies.
""")

PACK_VPK = Opt.boolean(
    'pack_vpk', False,
    """Allow files in VPKs to be packed into the map. 
    This is disabled by default since these are usually default files.
""")

PACK_DUMP = Opt.string_or_none(
    'pack_dump',
    """If set, copy all the packed resources to this location.
    You can also prefix this with a # character to only copy to this 
    destination, not the BSP pakfile.
    
    A folder is made inside named after the map, and is emptied each compile.
""")

PACK_STRIP_CUBEMAPS = Opt.boolean(
    'pack_strip_cubemaps', False,
    f"""If set, strip the generated cubemap files from the BSP. This is necessary for 2013-branch
    games to allow cubemaps to be built properly.
    
    This is equivalent to adding {CUBEMAP_REGEX!r} as a regex "pack_blocklist".
    """
)

PACK_ALLOWLIST = Opt.block(
    'pack_allowlist', Keyvalues('', []),
    """\
    Allows forcing specific files or folders to be packed. Each key in this block can be
    either a single file/folder, a glob-style pattern, or an arbitrary regex:
    
    * "path" "materials/models/props_expensive/"
    * "path" "scripts/game_sounds_ui.txt"
    * "glob" "*.nut"
    * "regex" "materials/(metal|concrete)/(courtyard|lobby)/*+\\.vmt"
    
    This overrides the blocklist, and also specifications in searchpaths.
    """,
)

PACK_BLOCKLIST = Opt.block(
    'pack_blocklist', Keyvalues('', []),
    """\
    Allows preventing specific files or folders from being packed. The format is the same as 
    'pack_allowlist'. Files generated by the postcompiler itself will always be packed. This will
    be checked against files already present in the BSP, so things like cubemaps can be removed.
    """,
)

SEARCHPATHS = Opt.block(
    'searchpaths', Keyvalues('', []),
    """\
    Specify additional locations to search for files, or configure whether existing locations pack
    or not. Each key-value pair defines a path, with the value either a folder path or a VPK 
    filename relative to the game root. You can also specify specific app ids that will get mounted with the <appid> operator.
    For example: <620>/portal2 will mount the portal2 folder from appid 620; that is Portal 2.
    The key defines the behaviour:
    * "prefix" "folder/" adds the path to the start, so it overrides all others.
    * "path" "vpk_path.vpk" adds the path to the end, so it is checked last.
    * "nopack" "somewhere*" matches against other definitions, disabling packing. Therefore you'll need
      to use this alongside other options, or use it to modify gameinfo paths. 
      It supports ?,* glob-style wildcards.
""")

SOUNDSCRIPT_MANIFEST = Opt.boolean(
    'soundscript_manifest', False,
    """Generate and pack game_sounds_manifest.txt, with all used soundscripts.     
    This is needed to make packing soundscripts work for the Portal 2 
    workshop.
    """,
)

PARTICLES_MANIFEST = Opt.boolean(
    'particles_manifest', False,
    """If enabled, generate and pack a particles manifest automatically.""",
)

MODEL_COMPILE_DUMP = Opt.string(
    'modelcompile_dump', '',
    """If set, models will be compiled as subfolders of this folder, instead of in a 
    temporary directory. The specified folder will be emptied at the start of each compile, to 
    prevent it filling up with old model sources. Move things out that you want to keep.
""")


PROPCOMBINE_QC_FOLDER = Opt.block(
    'propcombine_qc_folder',
    Keyvalues('', [Keyvalues('Path', f'|{PATH_KEY_GAME}|../content')]),
    """Define where the QC files are for combinable static props.
    This path is searched recursively. This defaults to 
    the 'content/' folder, which is adjacent to the game root.
    This is how Valve sets up their file structure.
""")

PROPCOMBINE_CROWBAR = Opt.boolean(
    'propcombine_crowbar', True,
    """If enabled, Crowbar will be used to decompile models which don't have
    a QC in the provided QC folder.
""")

PROPCOMBINE_CACHE = Opt.string(
    'propcombine_cache', f"|{PATH_KEY_GAME}|/decomp_cache/",
    """Cache location for models decompiled for combining."""
)

PROPCOMBINE_VOLUME_TOLERANCE = Opt.floating(
    'propcombine_volume_tolerance', -1.0,
    """When propcombining, an attempt will be made to merge collision meshes.
    
    If shrink wrapping a pair of meshes changes the volume less than this,
    the combined version will be used. If negative, this will not be done.
    """
)
PROPCOMBINE_MIN_AUTO_RANGE = Opt.integer(
    'propcombine_auto_range', 0,
    """If greater than zero, combine props at least this close together.""",
)
PROPCOMBINE_MAX_AUTO_RANGE = Opt.integer_or_none(
    'propcombine_max_auto_range',
    """If set, do not automatically combine props further away than this from each other.""",
)

PROPCOMBINE_MIN_CLUSTER = Opt.integer(
    'propcombine_min_cluster', 2,
    """The minimum number of props required before propcombine will
    bother merging them, in propcombine volumes. Should be greater than 1.
    """,
)

PROPCOMBINE_MIN_CLUSTER_AUTO = Opt.integer(
    'propcombine_min_cluster_auto', 0,
    """The minimum number of props required before the automatic propcombine clustering will
    merge the props. If less than or equal to 1, `propcombine_min_cluster` is used.
    """,
)

PROPCOMBINE_BLACKLIST = Opt.block(
    'propcombine_blacklist', Keyvalues('', []),
    """Models specified here will never be propcombined.

    You can specify a full path, or one with * wildcards. Alternatively,
    set 'no_propcombine' in the model $keyvalues.
    """,
)

PROPCOMBINE_PACK = Opt.boolean(
    'propcombine_pack', True,
    """If set, force-pack the combined props."""
)

PLUGINS = Opt.block(
    'plugins', Keyvalues('', []),
    """\
    Add plugins to the post compiler. Each block is a package of plugins in some folder.
    The name must be a Python identifier - the plugins are mounted at 
    "hammeraddons.bsp_transforms.plugin.blockname.filename".
    * "path" must be set to either a single Python file, or a folder of files.
    * If "recurse" is set, subfolders are recursively loaded as packages.
    The transforms folder inside the postcompiler folder is also always
    loaded, under the name "builtin".
""")

TRANSFORM_OPTS = Opt.block(
    'transform_opts', Keyvalues('', []),
    """Specify additional options specific to transforms. Each key here is the name of the 
    transform, and the value is then decided by that transform.
    """,
    deprecated=True,
)

DISABLED_TRANSFORMS = Opt.string(
    'transform_disable', '',
    """Specify transforms to disable as a comma-separated string."""
)
