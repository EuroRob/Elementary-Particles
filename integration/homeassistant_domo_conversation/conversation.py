"""The DOMO agent: hand the sentence to domo-router, say what it answers."""

import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_KEY, CONF_URL

_LOGGER = logging.getLogger(__name__)

# Serge is declined on the watch channel, so DOMO answers in a few seconds;
# past this the wrist has waited long enough.
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_UNREACHABLE = "DOMO isn't answering right now."


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DomoConversationEntity(entry)])


class DomoConversationEntity(conversation.ConversationEntity):
    _attr_has_entity_name = False
    _attr_name = "DOMO"

    def __init__(self, entry: ConfigEntry) -> None:
        self._url = entry.data[CONF_URL].rstrip("/")
        self._key = entry.data[CONF_KEY]
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["en"]   # DOMO's Whisper and answers are English only

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        try:
            async with async_get_clientsession(self.hass).post(
                f"{self._url}/ha/ask",
                json={"text": user_input.text, "ha_user_id": user_input.context.user_id},
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                answer = (await resp.json()).get("answer") or _UNREACHABLE
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("domo-router /ha/ask failed: %s", err)
            answer = _UNREACHABLE
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=answer)
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
