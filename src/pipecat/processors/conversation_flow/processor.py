#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from typing import List, Optional

from loguru import logger

from pipecat.frames.frames import (
    LLMMessagesAppendFrame,
    LLMMessagesUpdateFrame,
    LLMSetToolsFrame,
    TTSSpeakFrame,
)

from .flow import ConversationFlow


class ConversationFlowProcessor:
    """Processor that manages conversation flow based on function calls.

    This processor maintains conversation state and handles transitions between states
    based on LLM function calls. Each state (node) has its own message, available
    functions, and optional actions. The processor ensures the LLM context is updated
    appropriately as the conversation progresses.

    The flow is defined by a configuration that specifies:
    - Initial state
    - Available states (nodes)
    - Messages for each state
    - Available functions for each state
    - Optional actions for each state
    """

    def __init__(self, flow_config: dict, task, **kwargs):
        """Initialize the conversation flow processor.

        Args:
            flow_config: Dictionary containing the complete flow configuration,
                        including initial_node, nodes, and their configurations
        """
        super().__init__()
        self.flow = ConversationFlow(flow_config)
        self.initialized = False
        self.task = task

    async def initialize(self, initial_messages: List[dict]):
        """Initialize the conversation with starting messages and functions.

        This method sets up the initial context for the conversation, combining
        any system-level messages with the initial node's message and functions.

        Args:
            initial_messages: List of initial messages (typically system messages)
                            to include in the context

        Note:
            This must be called before the processor can handle any frames.
        """
        if not self.initialized:
            messages = initial_messages + [self.flow.get_current_message()]
            await self.task.queue_frame(LLMMessagesUpdateFrame(messages=messages))
            await self.task.queue_frame(LLMSetToolsFrame(tools=self.flow.get_current_functions()))
            self.initialized = True
            logger.debug(f"Initialized conversation flow at node: {self.flow.current_node}")
        else:
            logger.warning("Attempted to initialize ConversationFlowProcessor multiple times")

    async def register_functions(self, llm_service):
        """Register all functions from the flow configuration with the LLM service.

        This method sets up function handlers for all functions defined in the flow
        configuration. When a function is called, it will automatically trigger the
        appropriate state transition.

        Args:
            llm_service: The LLM service to register functions with
        """

        async def handle_function_call(
            function_name, tool_call_id, arguments, llm, context, result_callback
        ):
            await self.handle_transition(function_name)
            await result_callback("Acknowledged")

        # Register all functions from all nodes
        for node in self.flow.nodes.values():
            for function in node.functions:
                function_name = function["function"]["name"]
                llm_service.register_function(function_name, handle_function_call)

    async def handle_transition(self, function_name: str):
        """Handle state transition triggered by a function call.

        This method:
        1. Validates the function call against available functions
        2. Transitions to the new state if appropriate
        3. Executes any actions associated with the new state
        4. Updates the LLM context with new messages and available functions

        Args:
            function_name: Name of the function that was called
        """
        if not self.initialized:
            raise RuntimeError(
                "ConversationFlowProcessor must be initialized before handling transitions"
            )

        available_functions = self.flow.get_available_function_names()

        if function_name in available_functions:
            new_node = self.flow.transition(function_name)
            if new_node:
                if self.flow.get_current_actions():
                    await self._execute_actions(self.flow.get_current_actions())

                current_message = self.flow.get_current_message()

                await self.task.queue_frame(LLMMessagesAppendFrame(messages=[current_message]))
                await self.task.queue_frame(
                    LLMSetToolsFrame(tools=self.flow.get_current_functions())
                )

                logger.debug(f"Transition to {new_node} complete")
        else:
            logger.warning(
                f"Received invalid function call '{function_name}' for node '{self.flow.current_node}'. "
                f"Available functions are: {available_functions}"
            )

    async def _execute_actions(self, actions: Optional[List[dict]]) -> None:
        """Execute actions specified for the current node.

        Currently supports:
        - tts.say: Sends a TTSSpeakFrame with the specified text

        Args:
            actions: List of action configurations to execute
        """
        if not actions:
            return

        for action in actions:
            if action["type"] == "tts.say":
                logger.debug(f"Executing TTS action: {action['text']}")
                await self.task.queue_frame(TTSSpeakFrame(text=action["text"]))
            else:
                logger.warning(f"Unknown action type: {action['type']}")
