Task (3 PDFs: 1706.03762.pdf, 1810.04805.pdf, 1512.03385.pdf): For each PDF in the current directory, give: title; the core method in one sentence; datasets; the headline result with its number. At most 60 words per paper. Reply with the list only.

| head | total tokens | input | cache write | cache read | output | cost | turns | time |
|---|---|---|---|---|---|---|---|---|
| alone | 48,097 | 6 | 34,316 | 12,816 | 959 | $0.148 | 5 | 13s |
| modelmux | 19,747 | 6 | 7,082 | 11,683 | 976 | $0.039 | 3 | 62s |

### alone

1. **Attention Is All You Need** (1706.03762). Transformer: encoder-decoder built only from multi-head scaled dot-product self-attention plus feed-forward layers, with no recurrence or convolution. Datasets: WMT14 EN-DE (4.5M pairs), WMT14 EN-FR (36M), and English constituency parsing. Headline: 28.4 BLEU on EN-DE, over 2 BLEU above the previous best including ensembles. It also reaches 41.8 BLEU on EN-FR after 3.5 days on 8 GPUs.

2. **BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding** (1810.04805). Pre-trains a bidirectional Transformer encoder on masked-LM (15% of tokens masked) and next-sentence prediction, then fine-tunes all parameters with one added output layer per task. Pre-training data: BooksCorpus and English Wikipedia. Evaluated on GLUE, SQuAD v1.1, SQuAD v2.0 and SWAG. Headline: GLUE score 80.5%, a 7.7-point absolute gain. SQuAD v1.1 Test F1 is 93.2.

3. **Deep Residual Learning for Image Recognition** (1512.03385). ResNet: layers learn a residual F(x) = H(x) − x, added back through parameter-free identity shortcuts, which lets networks reach 152 layers. Datasets: ImageNet 2012, CIFAR-10, PASCAL VOC 07/12, MS COCO. Headline: an ensemble gets 3.57% top-5 error on the ImageNet test set, which won ILSVRC 2015. A single ResNet-152 gets 4.49% top-5 on validation.

### modelmux

- **Attention Is All You Need** (1706.03762): Transformer, a sequence-transduction architecture built only on multi-head self-attention, with no recurrence or convolutions. Datasets: WMT 2014 En-De, WMT 2014 En-Fr, Penn Treebank (WSJ). Headline: 28.4 BLEU on En-De (41.8 on En-Fr).

- **BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding** (1810.04805): Pre-trains deep bidirectional Transformers on unlabeled text with masked language modeling and next-sentence prediction. Datasets: BooksCorpus, English Wikipedia; evaluated on GLUE, SQuAD, SWAG, CoNLL-2003. Headline: GLUE 80.5% (+7.7% absolute).

- **Deep Residual Learning for Image Recognition** (1512.03385): Layers learn residual functions of their inputs via identity shortcut connections. Datasets: ImageNet, CIFAR-10, MS COCO, PASCAL VOC. Headline: 3.57% top-5 error on the ImageNet test set (1st place, ILSVRC 2015).
