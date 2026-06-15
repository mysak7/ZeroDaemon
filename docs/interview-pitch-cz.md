# ZeroDaemon — Pitch na 3 minuty (CZ)

Mluvený scénář (~450 slov, ~3 min v klidném tempu). V hranatých závorkách časy.
Pod scénářem: jeden diagram k nakreslení a tři follow-up otázky s odpověďmi.

---

## Scénář

**[0:00 — Hook]**

ZeroDaemon je lokální DevSecOps asistent řízený AI. Myšlenka je jednoduchá: místo
abych se přihlašoval na stroj a ručně pouštěl nmap, autonomní agent hlídá moje cíle,
detekuje drift a tahá threat intelligence, když něco vypadá podezřele — a samotné
skenování dělá z odpalitelné cloudové VM, ne z mého notebooku.

**[0:25 — Jaký problém řeší]**

Dva problémy. Za prvé: když skenuješ z domova, každý probe leze z tvojí vlastní IP —
špatné pro footprint i pro to, že tě začnou blokovat. Za druhé: bezpečnostní kontext
rychle stárne; čistý sken před půl rokem není fakt o dnešku. ZeroDaemon řeší obojí.

**[0:50 — Co to je, architektonicky]**

Controller běží lokálně — FastAPI plus agent nad LangGraphem. Když potřebuje skenovat,
provizní si malou **Kali Linux worker VM v cloudu**, na spot instanci, a nmap pustí na
ní přes SSH. Takže probe leze z odpalitelné cloudové IP, a jakmile je sken hotový, VM
se sama zničí na TTL. Když nic neběží, nestojí to nic.

**[1:25 — Na co jsem hrdý: dva agenti]**

Designové rozhodnutí, které bych vypíchnul: ve skutečnosti jsou tam *dva* agenti.
Hlavní umí jen skenovat. Všechno, co sahá na cloudovou infrastrukturu — postav VM,
zabij VM — žije v samostatném **builder sub-agentovi**, který je hlavní smyčce vystavený
jako jediný nástroj. Tím zůstává skenovací kontext čistý a každá akce měnící cloud je
za jednou auditovatelnou hranicí.

**[1:55 — Guardraily v kódu, ne v promptu]**

A zásadní věc — guardraily jsou vynucené v *kódu*, ne v promptu. Maximální počet
workerů, povolené typy strojů, povinné TTL, SSH zamčené jen na moji IP. Instrukce
v promptu je doporučení; vyhozená výjimka je zeď. Ať agenta ukecáš k čemukoli, fyzicky
nemůže nahodit flotilu ani 64jádrový stroj. Náklady i expozice jsou omezené už ze
své konstrukce.

**[2:30 — Paměť]**

Pro paměť je SQLite zdroj pravdy o aktuálním stavu a FAISS přidává sémantické
vyhledávání — ale každý dohledaný záznam je orazítkovaný svým stářím a zastaralá
threat-intel je označená. Agent vždycky ví, jak starý nález je.

**[2:50 — Závěr]**

Je model-agnostický — výchozí je Claude Fable 5, ale za běhu přepne na GPT, Gemini
nebo lokální model — běží jako trvalý daemon a přidat AWS nebo Azure jako worker backend
je jediná třída. To je ZeroDaemon.

---

## Jeden diagram (tohle nakresli)

```
  controller (lokálně)            cloud (GCP)
  ┌─────────────────┐    SSH    ┌──────────────┐
  │ FastAPI + agent │──────────►│ Kali VM      │
  │  + builder      │  gcloud   │ nmap (spot,  │
  │  + SQLite/FAISS │──────────►│  TTL pojistka)│
  └─────────────────┘           └──────────────┘
```

---

## Tři follow-up otázky, na které být připravený

**„Proč dva agenti místo jednoho promptu?"**
Oddělení zodpovědností a blast radius. Skener nikdy neřeší billing ani typy strojů;
builder nikdy neřeší porty. Jeden nástroj = jedna auditovatelná cloudová hranice, se
svým vlastním „šetrným" system promptem.

**„Co brání agentovi vyhnat obří cloudový účet?"**
Tvrdé limity v `WorkerManager.create_worker` — kontrolované před každým `gcloud`
voláním: allowlist typů strojů, strop počtu workerů a TTL, které se převede na
`--max-run-duration`, takže se VM smaže sama, i kdyby controller spadl. Spot ceny,
běžně jeden sdílený worker, nula nákladů v klidu.

**„Jak se vyhneš jednání podle zastaralé intel?"**
SQLite je autoritativní pro živý stav; FAISS recall je orazítkovaný stářím a zastaralá
threat-intel je explicitně označená, takže se stará data zváží, ne slepě věří.
