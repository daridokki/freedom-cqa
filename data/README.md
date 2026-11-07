# data/

Local-only folder for organizer datasets (e.g. `Freedom_CQA_DEV_65.json`). Its contents are git-ignored.

The DEV set contains 5 real voice calls, and their `audio_url` values carry signed access tokens to the recordings, so the files must not be published in a public repository. Import them through the UI («Загрузить JSON») or via the organizers' GET link («Импорт GET»).

For a quick demo without real data use `sample_data/synthetic_multilingual.json`.
