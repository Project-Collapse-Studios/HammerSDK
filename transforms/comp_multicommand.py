"""Implements simple logic."""
from srctools import conv_bool, conv_float, Entity, Output
from srctools.logger import get_logger

from hammeraddons.bsp_transform import trans, Context
from hammeraddons.bsp_transform.common import strip_cust_keys

@trans('comp_multi_command')
def comp_multi_command(ctx: Context):

    multicommands = ctx.vmf.by_class['comp_multi_command']

    if(len(multicommands) < 1):
        return

    for comp_ent in multicommands:

        command_caller = get_command_executor(ctx, comp_ent["type"].casefold())
        command_list:list[str] = []

        for i in range(16):
            command = comp_ent['command_' + (str(i))]
            if(len(command) < 1):
                continue
            command_list.append(command)
        joined_commands = "; ".join(command_list)

        match comp_ent['mode'].casefold():
            case 'spawn':
                comp_ent['classname'] = 'logic_auto'
                comp_ent['targetname'] = ''
                output = 'OnMapSpawn'
            case 'trigger':
                comp_ent['classname'] = 'logic_relay'
                output = 'OnTrigger'
            case _:
                raise ValueError(f'Invalid comp_multi_command mode "{comp_ent['mode']}"!')

        comp_ent.add_out(Output(output, command_caller, "Command", joined_commands, 0))
        strip_cust_keys(comp_ent)
 


def get_command_executor(ctx:Context, type:str) -> Entity:

    match type:
        case 'client':
            entity_name = 'point_clientcommand'
        case 'server':
            entity_name = 'point_servercommand'
        case 'multiplayer':
            entity_name = 'point_broadcastclientcommand'
        case _:
            raise ValueError(f'Invalid command entity type "{type}"!')

    command = ctx.vmf.by_target[entity_name]

    if(len(command) < 1): # If it doesn't exist, create one.
        command = ctx.vmf.create_ent(entity_name)
        command["targetname"] = f'cmp_multi_{entity_name}'

    return command
