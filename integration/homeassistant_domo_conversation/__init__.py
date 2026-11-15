"""DOMO as a Home Assistant conversation agent (Apple Watch Assist, 2026-10-30).

HA's Whisper turns the watch's audio into text, this agent asks domo-router
(POST /ha/ask) as the logged-in HA user, and HA's Piper speaks the answer.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
