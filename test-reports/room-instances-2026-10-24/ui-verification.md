# Room member instances: browser verification

Verified on 2026-10-24 with an isolated local fixture on port 49347, using agent-browser. No live model inference was used.

- Invited ChatGPT twice while a legacy ChatGPT member was already present; all three had separate member identities.
- Renamed the new members to 方案设计师 and 评审员. The source library name stayed ChatGPT.
- Uploaded a valid PNG through the instance avatar file input. Only 方案设计师 received the image; the source and sibling kept their defaults.
- Reloaded the page; both aliases and the uploaded avatar persisted.
- Removed 评审员 through its member settings. The source ChatGPT and 方案设计师 remained, including the custom avatar.
- Browser error collection was empty. Inspected the member settings screenshot for layout and image rendering.

The full regression report contains 286 tests: 284 passed, 2 skipped, zero failures or errors. It covers legacy room migration, instance limits and executor selection, source credential reuse, separate conversation keys, shared account quota, authenticated instance APIs, avatar isolation, alias export and targeted dispatch.

Updated the running application on port 49315 after confirming no rooms were running or queued. Verified the existing rooms were retained and the new name/avatar controls were served. The isolated fixture server and browser session were stopped after verification.
