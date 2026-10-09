# Set B2 OCG correction

Research finding, not production OCG extraction.

The earlier Set B2 report stated that all four PDFs lacked Optional Content
Groups (OCGs). RH1 disproved that statement by an exact byte-level check of the
unchanged input files. Two PDFs contain both `/OCProperties` and an `/OCGs`
indirect-reference array.

| file | SHA-256 | `/OCProperties` | `/OCGs` | references in array | RH1 status |
|---|---|---:|---:|---:|---|
| `2311_011B_Asematie_assari.pdf` | `0ee9fcfa9bc427fa203aeccb987d293c41d7753bc88bdcd15262d93c556e87a9` | 0 | 0 | — | `NOT_VERIFIED` |
| `2311_012B_Helsingintie_assari.pdf` | `ebbabec1d52906ef10b4b7928e43b18db302418c8a4133f131a428e3b432ffb6` | 0 | 0 | — | `NOT_VERIFIED` |
| `2311_752A_val.pdf` | `f7e5679a18e4242a53ecc908fc0fc28fd3f8bfbab362557a830f5c94d430eb6e` | 1 at byte 13,825 | 1 at byte 52,697 | 4,307 | `PRESENT` |
| `IR208122_KTT_2308_012_LPA_asemap_B.pdf` | `93c0a2ec22f9efd18c08308fefe07355f723194aad3f63e13c6be9b72187d0f8` | 1 at byte 42 | 1 at byte 1,353 | 171 | `PRESENT` |

The reference counts are counts of syntactically matching `n 0 R` entries in
the `/OCGs [...]` arrays. They are not a claim that PDFium exposed or validated
every OCG object. The current PoC output itself still says that source
layer/OCG is unavailable through the public PDFium API.

Absence of those two byte tokens is **not** reported as `ABSENT`: the RH1 scan
is not a complete PDF parser and cannot establish absence through every
possible object-stream representation. Those files therefore remain
`NOT_VERIFIED`, not `ABSENT`.

Reproduction command (read-only):

```powershell
@'
import hashlib, os, re
root = r'benchmark\Set_B2_Test'
for name in sorted(os.listdir(root)):
    if not name.lower().endswith('.pdf'):
        continue
    data = open(os.path.join(root, name), 'rb').read()
    print(name, hashlib.sha256(data).hexdigest())
    for token in (b'/OCProperties', b'/OCGs'):
        print(token.decode(), data.count(token),
              [m.start() for m in re.finditer(re.escape(token), data)])
    m = re.search(rb'/OCGs\s*\[(.{0,200000}?)\]', data, re.S)
    print('references', len(re.findall(rb'\d+\s+0\s+R', m.group(1)))
          if m else None)
'@ | python -
```
