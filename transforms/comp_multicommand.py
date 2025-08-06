"""Implements simple logic."""
from srctools import conv_bool, conv_float, Entity, Output
from srctools.logger import get_logger

from hammeraddons.bsp_transform import trans, Context

@trans('comp_multi_client_command')
def comp_multi_client_command(ctx: Context):

    multicommands = ctx.vmf.by_class['comp_multi_client_command']
    if(len(multicommands) < 1):
        return

    clientcommand = get_command_executor(ctx, "client")

    for comp_ent in multicommands:
        relay = process_multicommand_entties(ctx, comp_ent, clientcommand)
        relay["targetname"] = comp_ent["targetname"]
    pass

@trans('comp_multi_server_command')
def comp_multi_server_command(ctx: Context):

    multicommands = ctx.vmf.by_class['comp_multi_server_command']
    if(len(multicommands) < 1):
        return

    servercommand = get_command_executor(ctx, "server")

    for comp_ent in multicommands:
        relay = process_multicommand_entties(ctx, comp_ent, servercommand)
        relay["targetname"] = comp_ent["targetname"]
    pass

@trans('comp_auto_multi_client_command')
def comp_auto_multi_client_command(ctx: Context):

    multicommands = ctx.vmf.by_class['comp_auto_multi_client_command']
    if(len(multicommands) < 1):
        return

    clientcommand = get_command_executor(ctx, "client")

    for comp_ent in multicommands:
        relay = process_multicommand_entties(ctx, comp_ent, clientcommand)
        relay["spawnflags"] = 1 # We want this entity to delete itself.
        relay.add_out(Output("OnSpawn","!self","Trigger","",0))
    pass

@trans('comp_auto_multi_server_command')
def comp_auto_multi_server_command(ctx: Context):

    multicommands = ctx.vmf.by_class['comp_auto_multi_server_command']
    if(len(multicommands) < 1):
        return

    servercommand = get_command_executor(ctx, "server")

    for comp_ent in multicommands:
        relay = process_multicommand_entties(ctx, comp_ent, servercommand)
        relay["spawnflags"] = 1 # We want this entity to delete itself.
        relay.add_out(Output("OnSpawn","!self","Trigger","",0))
    pass


def process_multicommand_entties(ctx:Context, comp_ent: Entity, command_caller: Entity) -> 'Entity':

    comp_ent.remove()
    command_list:list[str] = []
    for i in range(16):
        command_list.append(comp_ent['command_' + (str(i))])
    joined_commands = "; ".join(command_list)
    relay = ctx.vmf.create_ent("logic_relay")
    relay.add_out(Output("OnTrigger", command_caller, "Command", joined_commands, 0))

    return relay

def get_command_executor(ctx:Context, type:str) -> Entity:

    command = ctx.vmf.by_target['cmp_multi_'+type+'command']

    if(len(command) < 1): # If it doesn't exist, create one.
        command = ctx.vmf.create_ent('point_'+type+'command')
        command["targetname"] = 'cmp_multi_'+type+'command'

    return command
