# Standardizing health data

Standardization makes readings from different sources comparable when their
meaning and units match. It runs offline and needs no model API key.

## Names, codes and units

- Measurements use the bundled LOINC vocabulary.
- Reported symptoms and diagnoses use separate ICPC-3 lookups.
- Units are normalized to UCUM; conversions require compatible dimensions.
- Multilingual aliases can resolve to the same concept. An unknown or
  ambiguous name can remain unresolved.

Passing the original unit is important: it can affect which code represents
a reading. Similar labels do not guarantee the same clinical meaning.

## Python example

```python
from mirobody import resolve

result = resolve("hemoglobin")
print(result.loinc)  # 718-7
```

Use the original value and unit when standardizing a complete reading.
For document extraction and library examples, see the
[quickstart](../quickstart.md).

## Scope

The bundled vocabulary covers selected laboratory and clinical observations,
not every LOINC concept or vendor score. Incompatible units are not silently
converted, and an unresolved name does not receive a guessed nearest code.
You can inspect the corpus identifier through `mirobody.BUNDLE_VERSION`.

Vocabulary coverage, licensing and mapping rules are documented in the
[standardization reference](../standardization.md).
