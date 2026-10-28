# YOLO detection examples

These detector outputs match four of the screenshots supplied by the project
author. They were copied without edits from the local
`neu_yolo_debug_oai_dev_best_n_1.0` output directory.

| Repository asset | Original output identifier | Displayed confidence |
| --- | --- | --- |
| `crescent-gap-071.png` | `crescent_gap_1_0005` | 0.71 |
| `crescent-gap-069.png` | `crescent_gap_2_0002` | 0.69 |
| `oil-spot-043.png` | `oil_spot_4_0002` | 0.43 |
| `crease-055.png` | `crease_4_0002` | 0.55 |

Each source filename follows the pattern
`point_2__gc10_dataset_oai_dev_IDENTIFIER__gc10_dataset_oai_dev_IDENTIFIER.png`.
The red boxes, class labels, and confidence scores are preserved as recorded.

These are model predictions, distinct from the green ground-truth boxes in the
[dataset examples](../gc10-det/README.md). Selected screenshots do not establish
aggregate accuracy, detection quality across classes, or deadline compliance.

Source dataset: [GC10-DET](https://github.com/lvxiaoming2019/GC10-DET-Metallic-Surface-Defect-Datasets).
Dataset paper: [Deep Metallic Surface Defect Detection: The New Benchmark and Detection Network](https://doi.org/10.3390/s20061562).
