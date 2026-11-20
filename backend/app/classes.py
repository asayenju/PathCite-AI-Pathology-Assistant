"""Class index -> tissue name map for the fine-tuned ResNet18.

The training notebook stored classes as '0'..'8' without names. This map was
verified against the `label` and `tissue` columns of
chehablab/NCT-CRC-HE-100K (test split, via the Hub datasets-server /rows API):
each class is a contiguous block whose boundaries match the per-class support
in the notebook's external-test report (1338, 847, 339, 634, 1035, 592, 741,
421, 1233).
"""

CLASS_NAMES: tuple[str, ...] = (
    "ADI",   # 0 adipose
    "BACK",  # 1 background
    "DEB",   # 2 debris
    "LYM",   # 3 lymphocytes
    "MUC",   # 4 mucus
    "MUS",   # 5 smooth muscle
    "NORM",  # 6 normal colon mucosa
    "STR",   # 7 cancer-associated stroma
    "TUM",   # 8 colorectal adenocarcinoma epithelium
)
