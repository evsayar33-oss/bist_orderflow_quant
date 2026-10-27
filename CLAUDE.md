# Task Observer activation

Invoke the `task-observer` skill before the FIRST tool call of every session, and before writing or proposing a plan.

- Workspace folder (pinned to the repo): `<repo root>/.claude`. Resolve the absolute path at session start with `git rev-parse --show-toplevel` — the checkout lives at a different location per environment (for example `/home/claude/bist_orderflow_quant` or `/home/user/bist_orderflow_quant`), so a fixed path is wrong somewhere.
- Observation log: `<repo root>/.claude/skill-observations/observation-log/`
- Substitute the resolved `<repo root>/.claude` for `[ABSOLUTE PATH]` in every skill snippet. Keep it in double quotes. Run snippets with bash.
- Commit new observation files to the repo so they persist between sessions.

# Gerçekçilik kuralı (sahibin isteği — tüm çalışmalar)

Sahibe canlıda en iyi çalışacak sonuç lazım, testte en iyi görüneni değil.
- Kabul kuralını sonuçlara bakmadan ÖNCE yaz (alt dönemlerin hepsinde geçmeli, komşu ayarlarda da geçmeli, yüksek maliyette de geçmeli).
- Çok deneme = şans riski (aşırı uyum). Deneme sayısını söyle; tek en iyi noktayı değil, düz/sağlam bölgeyi seç.
- Daha iyisi kurala göre çıkmazsa açıkça yaz: "daha iyisi bulunamadı" veya "canlıda aynısı olmayabilir, bu yüzden önermiyorum".
- Zayıf yönleri (geride kalınan dönemler, test edilemeyen veriler, tek sektöre bağımlılık) her raporda yaz.
