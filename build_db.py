"""下载词典原始数据，合成查词弹窗用的 data/dict.db（SQLite）：
  - ECDICT（英汉，MIT）               https://github.com/skywind3000/ECDICT
  - Wiktionary（英英，CC BY-SA 4.0）   kaikki.org 的英语导出 https://kaikki.org/dictionary/English/
  - Open English WordNet（英英后备与同义词，CC BY 4.0）https://en-word.net/

    python build_db.py                         下载（已下载的跳过）并全部重建
    python build_db.py wiktionary thesaurus    只重建指定部分

原始数据下载到 download/（约 700MB），构建完成后可以删除。setup.ps1 会自动调用本脚本。
"""
import csv
import gzip
import json
import re
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
DOWNLOAD = ROOT / "download"
SRC = DOWNLOAD / "ecdict.csv"
WIKT_SRC = DOWNLOAD / "wiktionary-en.jsonl.gz"
WN_DIR = DOWNLOAD / "wn"
DST = ROOT / "data" / "dict.db"

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
WIKT_URL = "https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz"
WN_LEXICON = "oewn:2024"

FIELDS = ["word", "phonetic", "definition", "translation", "pos",
          "collins", "oxford", "tag", "bnc", "frq", "exchange"]


def download(url, dest):
    """下载到 dest（先写临时文件，完成后再改名，中断后重跑会重新下载）。"""
    if dest.exists():
        print(f"已存在，跳过：{dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"下载 {url}")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total:6.1%}  {done >> 20} / {total >> 20} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def fetch_sources():
    download(ECDICT_URL, SRC)
    download(WIKT_URL, WIKT_SRC)
    import wn
    wn.config.data_directory = str(WN_DIR)
    try:
        have = bool(wn.lexicons(lexicon=WN_LEXICON))
    except wn.Error:
        have = False
    if not have:
        print(f"下载 WordNet（{WN_LEXICON}）")
        wn.download(WN_LEXICON)


def main():
    steps = {"wiktionary": build_wiktionary, "thesaurus": build_thesaurus}
    if sys.argv[1:]:
        db = sqlite3.connect(DST)
        for name in sys.argv[1:]:
            steps[name](db)
        db.execute("VACUUM")
        db.close()
        return

    fetch_sources()
    DST.parent.mkdir(exist_ok=True)
    if DST.exists():
        DST.unlink()
    csv.field_size_limit(sys.maxsize)
    db = sqlite3.connect(DST)
    db.execute(f"CREATE TABLE dict ({', '.join(f + ' TEXT' for f in FIELDS)}, sw TEXT)")
    with open(SRC, encoding="utf-8", newline="") as f:
        rows = ((*(r.get(k, "") for k in FIELDS), r["word"].lower())
                for r in csv.DictReader(f))
        db.executemany(f"INSERT INTO dict VALUES ({', '.join('?' * (len(FIELDS) + 1))})", rows)
    db.execute("CREATE INDEX idx_sw ON dict (sw)")
    db.commit()
    print(db.execute("SELECT count(*) FROM dict").fetchone()[0], "ECDICT entries")

    build_wordnet(db)
    build_thesaurus(db)
    build_wiktionary(db)
    db.execute("VACUUM")
    db.close()


POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


def pick_example(examples, forms):
    """只保留包含该词（或其变形）的例句，避免显示同义词的例句。"""
    pat = re.compile(r"\b(" + "|".join(re.escape(f) for f in forms) + r")\w*\b", re.I)
    for ex in examples:
        if pat.search(ex):
            return ex
    return None


def open_wordnet():
    import wn
    wn.config.data_directory = str(WN_DIR)
    return wn.Wordnet(WN_LEXICON)


def build_thesaurus(db):
    """同义词：WordNet 每个义项（synset）里的其他词，形容词再加上 "similar to" 的词。"""
    en = open_wordnet()
    entries = defaultdict(lambda: defaultdict(list))
    for i, word in enumerate(en.words()):
        lemma = word.lemma()
        pos = POS_NAMES.get(word.pos, word.pos)
        for sense in word.senses():
            syn = sense.synset()
            words = [w for w in syn.lemmas() if w.lower() != lemma.lower()]
            if word.pos in "as":
                for sim in syn.get_related("similar"):
                    words += [w for w in sim.lemmas() if w.lower() != lemma.lower()]
            words = list(dict.fromkeys(w.replace("_", " ") for w in words))[:10]
            if not words:
                continue
            item = {"s": words}
            ex = pick_example(sense.examples() + syn.examples(), word.forms())
            if ex:
                item["e"] = ex
            entries[lemma.lower()][pos].append(item)
        if i % 20000 == 0:
            print(f"  thesaurus {i}…", flush=True)

    db.execute("DROP TABLE IF EXISTS thesaurus")
    db.execute("CREATE TABLE thesaurus (sw TEXT PRIMARY KEY, data TEXT)")
    rows = []
    for sw, by_pos in entries.items():
        groups = sorted(by_pos.items(), key=lambda kv: -len(kv[1]))
        rows.append((sw, json.dumps([{"pos": p, "senses": s[:12]} for p, s in groups], ensure_ascii=False)))
    db.executemany("INSERT INTO thesaurus VALUES (?, ?)", rows)
    db.commit()
    print(len(rows), "thesaurus entries")


def build_wordnet(db):
    en = open_wordnet()

    # sw -> pos -> [{"d": 定义, "e": 例句}]
    entries = defaultdict(lambda: defaultdict(list))
    for i, word in enumerate(en.words()):
        pos = POS_NAMES.get(word.pos, word.pos)
        forms = word.forms()
        bucket = entries[word.lemma().lower()][pos]
        for sense in word.senses():
            syn = sense.synset()
            item = {"d": syn.definition() or ""}
            ex = pick_example(sense.examples() + syn.examples(), forms)
            if ex:
                item["e"] = ex
            bucket.append(item)
        if i % 20000 == 0:
            print(f"  wordnet {i}…")

    db.execute("CREATE TABLE wordnet (sw TEXT PRIMARY KEY, data TEXT)")
    rows = []
    for sw, by_pos in entries.items():
        # 义项多的词性排前面（run：动词 41 条在名词 16 条之前）
        groups = sorted(by_pos.items(), key=lambda kv: -len(kv[1]))
        rows.append((sw, json.dumps([{"pos": p, "senses": s} for p, s in groups], ensure_ascii=False)))
    db.executemany("INSERT INTO wordnet VALUES (?, ?)", rows)
    db.commit()
    print(len(rows), "WordNet entries")


WIKT_POS = {"noun": "noun", "verb": "verb", "adj": "adjective", "adv": "adverb",
            "prep": "preposition", "conj": "conjunction", "pron": "pronoun", "det": "determiner",
            "intj": "exclamation", "num": "number", "particle": "particle", "article": "article",
            "phrase": "phrase", "prep_phrase": "phrase", "proverb": "proverb",
            "abbrev": "abbreviation", "contraction": "contraction", "name": "proper noun"}
SKIP_TAGS = {"obsolete", "form-of", "alt-of", "misspelling", "nonstandard", "rare", "archaic"}
MAX_SENSES = 25


def clean_example(ex):
    """只要简短的示例句，不要带出处的长篇文献引文。"""
    text = (ex.get("text") or "").strip()
    if ex.get("ref") or ex.get("type") == "quotation" or not text or len(text) > 140 or "\n" in text:
        return None
    return text


def wikt_senses(entry):
    out = []
    for s in entry.get("senses", []):
        if SKIP_TAGS & set(s.get("tags") or []) or not s.get("glosses"):
            continue
        gloss = s["glosses"][-1].strip()
        raw = (s.get("raw_glosses") or s["glosses"])[-1].strip()
        # raw_glosses 开头括号里是用法标签，如 "(intransitive) To move ..."
        m = re.match(r"^\(([^()]{1,40})\)\s*", raw)
        item = {"d": gloss}
        if m:
            item["l"] = [m.group(1)]
        for ex in s.get("examples") or []:
            text = clean_example(ex)
            if text:
                item["e"] = text
                break
        out.append(item)
        if len(out) >= MAX_SENSES:
            break
    return out


def pick_ipa(entry):
    sounds = [s for s in entry.get("sounds") or [] if s.get("ipa")]
    for s in sounds:
        if {"General-American", "US"} & set(s.get("tags") or []):
            return s["ipa"]
    return sounds[0]["ipa"] if sounds else ""


ORIGIN_START = re.compile(
    r"^(From|Coined|Borrowed|Named|Clipping|Contraction|Short|Blend|Uncertain|Unknown|Probably|Possibly|"
    r"Perhaps|Back-formation|Abbreviation|Derived|Compound|Ultimately|Of uncertain|Originally|Alteration|"
    r"Learned|Calque|Inherited|Onomatopoeic|Imitative|Apparently|Origin|Variant)\b")


def clean_origin(text):
    """去掉 "Etymology tree" 之类的结构化残留，只保留正文的第一段。"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if lines and lines[0].startswith("Etymology tree"):
        start = next((i for i, l in enumerate(lines) if ORIGIN_START.match(l)), None)
        lines = lines[start:start + 1] if start is not None else []
    text = lines[0] if lines else ""
    if len(text) > 320:
        text = text[:320].rsplit(" ", 1)[0] + "…"
    return text


def build_wiktionary(db):
    # 只收录 ECDICT 或 WordNet 里有的词，控制数据库大小
    known = {r[0] for r in db.execute("SELECT sw FROM dict")}
    known |= {r[0] for r in db.execute("SELECT sw FROM wordnet")}

    # sw -> {"groups": {pos: [senses]}, "ipa", "origin"}
    entries = {}
    with gzip.open(WIKT_SRC, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i % 200000 == 0:
                print(f"  wiktionary {i}…", flush=True)
            d = json.loads(line)
            if d.get("lang_code") != "en":
                continue
            word, pos = d.get("word", ""), WIKT_POS.get(d.get("pos"))
            sw = word.lower()
            if not pos or sw not in known:
                continue
            senses = wikt_senses(d)
            if not senses:
                continue
            e = entries.setdefault(sw, {"groups": {}, "ipa": "", "origin": "", "hyph": ""})
            e["groups"].setdefault(pos, []).extend(senses)
            e["ipa"] = e["ipa"] or pick_ipa(d)
            # 音节划分，如 hap·py；新旧两种导出格式都处理
            parts = ((d.get("hyphenations") or [{}])[0].get("parts") or d.get("hyphenation") or [])
            if not e["hyph"] and len(parts) > 1 and "".join(parts).lower() == sw:
                e["hyph"] = "·".join(parts)
            e["origin"] = e["origin"] or clean_origin(d.get("etymology_text"))

    db.execute("DROP TABLE IF EXISTS wiktionary")
    db.execute("CREATE TABLE wiktionary (sw TEXT PRIMARY KEY, data TEXT)")
    rows = []
    for sw, e in entries.items():
        groups = e["groups"]
        # 专有名词只在没有其他词性时保留（避免 rose 下面列出人名 Rose）
        if len(groups) > 1:
            groups.pop("proper noun", None)
        ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        rows.append((sw, json.dumps({
            "en": [{"pos": p, "senses": s[:MAX_SENSES]} for p, s in ordered],
            "ipa": e["ipa"].strip("/[]"),
            "origin": e["origin"],
            "hyph": e["hyph"],
        }, ensure_ascii=False)))
    db.executemany("INSERT INTO wiktionary VALUES (?, ?)", rows)
    db.commit()
    print(len(rows), "Wiktionary entries")


if __name__ == "__main__":
    main()
