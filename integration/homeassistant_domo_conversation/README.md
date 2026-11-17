# DOMO conversation agent for Home Assistant

Lets Apple Watch Assist (HA companion app) talk to DOMO. Live 2026-10-30 on the main HA (10.10.30.14)
as `custom_components/domo_conversation`, entity `conversation.domo`, Assist pipeline **"DOMO"**.

```
Watch -> iPhone (HA app) -> stt.gpu02_whisper (Wyoming bridge on gpu-02:10300
         -> Whisper large-v3-turbo on CUDA, ~1 s; integration/wyoming_whisper_bridge)
      -> local intents first (lights, temperatures: <1 s)
      -> else conversation.domo -> domo-router POST /ha/ask (channel "watch")
      -> HA Piper (tts.piper, en_US-amy-low) -> Watch
```

- **On premise:** leave the companion app's *on-device* speech recognition and
  *on-device* TTS options **off** (their default). With them on, Apple's speech
  services are used instead of our Whisper and Piper.
- **Who is speaking:** the HA user logged into the app. domo-router maps HA user
  ids to speakers (`DOMO_HA_USERS_JSON`, set in the assistant_stack role):
  admin -> owner, Partner -> partner. Any other user gets "I don't know who you are yet".
- **Auth:** HA's own key, Vault `secret/domo/ha` -> `ha_key`, stored in the config
  entry. The gateway's router key and the phone tokens are not accepted on `/ha/ask`.
- **Too slow for a wrist:** routes `serge` and `scout` (~15 s) are declined with
  "Ask me on your phone". Everything else answers in a few seconds.
- **Depends on gpu-02:** the router's classifier and (since 2026-10-30) all of HA's
  speech-to-text run there. ep_probes `gpu02-ollama`, `gpu02-whisper`,
  `ha-stt-bridge` and `domo-gateway` alert within ~10 min if it goes away.
- **Speech-to-text, all three pipelines** (DOMO, Mixtra-Ollama, Home Assistant) use
  `stt.gpu02_whisper`. HA's own Whisper add-on (base model, CPU, 1.3 GB RAM) is
  stopped with boot=manual and its Wyoming entry disabled. Roll back: start the
  add-on, re-enable the "Whisper" Wyoming entry, set the pipelines' STT back to
  `stt.faster_whisper`.

Setup used (repeatable): copy this folder to `/config/custom_components/domo_conversation`,
restart HA Core, create the entry via the config-flow API (url `http://10.10.30.20:5140` +
key), then create the pipeline over the websocket (`assist_pipeline/pipeline/create`,
same STT/TTS as "Mixtra-Ollama", `conversation_engine: conversation.domo`,
`prefer_local_intents: true`). On the watch: HA app -> Assist -> pipeline "DOMO".
