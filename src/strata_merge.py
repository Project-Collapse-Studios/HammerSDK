"""Script to assist with merging Strata's fork back into upstream."""
from pathlib import Path
import io
import difflib

from srctools.filesys import RawFileSystem
from srctools import FGD

MERGED = {  # Set of classnames we have checked already and know the diff is fine.
    'BaseEntityVisBrush',
    'BaseHeadcrab', 'BaseHelicopter', 'BasePortButton', 'BaseTrain', 'CombineScanner',

    'ai_changetarget', 'ai_goal_actbusy', 'ai_goal_injured_follow', 'ai_goal_lead_weapon',
    'ai_script_conditions', 'aiscripted_schedule', 'ambient_generic',

    'color_correction',
    'comp_adv_output', 'comp_case', 'comp_entity_finder', 'comp_kv_setter', 'comp_prop_cable',
    'comp_prop_cable_dynamic', 'comp_prop_rope', 'comp_prop_rope_dynamic', 'comp_propcombine_set',
    'comp_propcombine_volume', 'comp_relay', 'comp_sequential_call', 'comp_trigger_coop',
    'comp_vactube_start', 'comp_vactube_junction', 'comp_vactube_end', 'comp_vactube_sensor',
    'comp_vactube_spline', 'comp_vactube_object',
    'env_cubemap', 'env_effectscript', 'env_fade', 'env_fire', 'env_firesensor', 'env_fog_controller',
    'env_projectedtexture', 'env_portal_laser', 'env_soundscape',
    'filter_enemy', 'filter_multi', 'func_areaportal', 'func_breakable', 'func_breakable_surf',
    'func_brush', 'func_combine_ball_spawner', 'func_door', 'func_illusionary', 'func_physbox',
    'func_instance', 'func_instance_io_proxy',
    'func_movelinear', 'linked_portal_door',
    'item_ammo_357', 'item_ammo_357_large',
    'item_ammo_ar2', 'item_ammo_ar2_altfire', 'item_ammo_ar2_large',
    'item_ammo_crossbow', 'info_coop_spawn', 'info_apc_missile_hint',
    'logic_achievement', 'logic_choreographed_scene', 'logic_compare',
    'prop_thumper', 'path_track', 'prop_laser_catcher', 'prop_testchamber_sign', 'prop_tractor_beam',
    'prop_testchamber_door', 'paint_sphere',
    'point_bonusmaps_accessor', 'point_broadcastclientcommand', 'point_camera', 'point_changelevel',
    'npc_pigeon', 'npc_rollermine', 'npc_seagull', 'npc_security_camera', 'npc_strider', 'npc_zombine',
    'trigger_soundoperator', 'trigger_soundscape',
    'trigger_togglesave', 'trigger_remove', 'trigger_rpgfire', 'trigger_tonemap', 'trigger_transition',
    'trigger_wind', 'trigger_weapon_dissolve', 'trigger_weapon_strip',
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
    with open(REPORT_DIR / '.gitignore', 'w') as f:
        f.write('*')

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
        try:
            strata_ent = strata_fgd[classname]
        except KeyError:
            removed.append(classname)
            continue

        # We added this to lots of ents, but that's not in strata.
        for tags_map in ha_ent.keyvalues.values():
            for kv in tags_map.values():
                kv.reportable = False
        # Sort helpers, color() ones in particular are misordered.
        ha_ent.helpers.sort(key=repr)
        strata_ent.helpers.sort(key=repr)

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
                f'HammerAddons - {classname}', f'Strata - {classname}',
            ))
        count += 1
    print(f'Conflicts: {count}')
    print(f'Added: {added}')
    print(f'Removed: {removed}')


if __name__ == '__main__':
    main()
