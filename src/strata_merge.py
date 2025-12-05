"""Script to assist with merging Strata's fork back into upstream."""
from pathlib import Path
import io
import difflib

from srctools.filesys import RawFileSystem
from srctools import FGD

MERGED = {  # Set of classnames we have merged already.
    'BaseEntityVisBrush',
    'BaseHeadcrab',
    'BaseHelicopter',
    'BasePortButton',
    'BaseTrain',
    'CombineScanner',
    'filter_enemy', 'filter_multi', 'func_areaportal', 'func_breakable', 'func_breakable_surf',
    'func_brush', 'func_combine_ball_spawner', 'func_door', 'func_illusionary',
    'func_instance', 'func_instance_io_proxy',
    'func_movelinear',
    'item_ammo_357', 'item_ammo_357_large',
    'item_ammo_ar2', 'item_ammo_ar2_altfire', 'item_ammo_ar2_large',
    'item_ammo_crossbow',
}

REPORT_DIR = Path('..', 'strata_merge').resolve()


def main() -> None:
    """Check all the FGDs."""
    fsys = RawFileSystem('F:/SteamLibrary/SteamApps/common/Portal 2 Community Edition/p2ce')
    strata_fgd = FGD()
    strata_fgd.parse_file(fsys, fsys['p2ce.fgd'], encoding='iso-8859-1')
    fsys = RawFileSystem('../build/')
    ha_fgd = FGD()
    ha_fgd.parse_file(fsys, fsys['p2ce.fgd'], encoding='iso-8859-1')

    for fname in REPORT_DIR.iterdir():
        fname.unlink()

    classes = (strata_fgd.entities.keys() | ha_fgd.entities.keys())
    print(f'{len(classes)} entities defined.')
    classes -= MERGED
    added = []
    removed = []
    count = 0
    for classname in classes:
        try:
            ha_ent = ha_fgd[classname]
        except KeyError:
            added.append(classname)
            continue
        for tags_map in ha_ent.keyvalues.values():
            for kv in tags_map.values():
                kv.reportable = False

        try:
            strata_ent = strata_fgd[classname]
        except KeyError:
            removed.append(classname)
            continue
        with io.StringIO() as ha_buf:
            ha_ent.export(ha_buf)
            ha_text = ha_buf.getvalue()
        with io.StringIO() as strata_buf:
            strata_ent.export(strata_buf)
            strata_text = strata_buf.getvalue()
        if ha_text.casefold() == strata_text.casefold():
            continue

        with open(REPORT_DIR / f'{classname}.diff', 'w') as f:
            f.writelines(difflib.unified_diff(
                ha_text.splitlines(keepends=True), strata_text.splitlines(keepends=True),
                'HammerAddons', 'Strata',
            ))
        count += 1
    print(f'Conflicts: {count}')
    print(f'Added: {added}')
    print(f'Removed: {removed}')


if __name__ == '__main__':
    main()
