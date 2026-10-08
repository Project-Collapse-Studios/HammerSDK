"""Implements shutdown/activation delays on varius test element entities, including laser catchers, relays, buttons, etc."""

from hammeraddons.bsp_transform import trans, Context
from srctools.logger import get_logger
from srctools import Entity, VMF, Output, conv_float


LOGGER = get_logger(__name__)

# It's very important that the IO specified below is in a proper order
# For example, onpowered has to have the same index as onunpowered

# Valid outputs for shutdown delayers

shutdown_delayer_outputs = [
    "OnUnpowered", "OnUnPressed"
]

# Valid outputs for activation delayers

activation_delayer_outputs = [
    "OnPowered", "OnPressed"
]

@trans("delayers")
def create_delayers(ctx) -> None:
    vmf: VMF = ctx.vmf

    entity: Entity
    for entity in vmf.entities:
        
        activation_delay: float = conv_float(entity["activation_delay", 0])
        shutdown_delay: float = conv_float(entity["shutdown_delay", 0])

        if activation_delay:

            processed_outputs = set()
            for output in entity.outputs:
                try:
                    out_idx = [x.casefold() for x in activation_delayer_outputs].index(output.output.casefold())
                except ValueError:
                    out_idx = -1

                if out_idx >= 0:
                    output.delay += activation_delay

                    if not output in processed_outputs:
                        entity.add_out(
                            Output(shutdown_delayer_outputs[out_idx], "!self", "CancelPending")
                        )
                        processed_outputs.add(output.output)


        if shutdown_delay:

            processed_outputs = set()
            for output in entity.outputs:
                try:
                    out_idx = [x.casefold() for x in shutdown_delayer_outputs].index(output.output.casefold())
                except ValueError:
                    out_idx = -1

                if out_idx >= 0:
                    output.delay += shutdown_delay

                    if not output in processed_outputs:
                        entity.add_out(
                            Output(activation_delayer_outputs[out_idx], "!self", "CancelPending")
                        )
                        processed_outputs.add(output.output)



        
                



