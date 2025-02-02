import torch
import numpy as np
from transformers import XLMRobertaForSequenceClassification, XLMRobertaTokenizer


class PythonPredictor:
    def __init__(self, config):
        model_name = config.get("model_name", None)
        model_path = config.get("model_path", None)
        device = config.get("device", 0)  # default on gpu 0
        self.tokenizer = XLMRobertaTokenizer.from_pretrained(model_path)
        # the default entailment id is 2 (contradiction is 0, neutral is 1)
        self.contradiction_id = 0
        self.entailment_id = 2
        self.model = XLMRobertaForSequenceClassification.from_pretrained(model_path)
        self.model.eval()
        self.model.half()
        self.device = torch.device("cpu" if device < 0 else "cuda:{}".format(device))
        if self.device.type == "cuda":
            self.model = self.model.to(self.device)

    def ensure_tensor_on_device(self, **inputs):
        return {name: tensor.to(self.device) for name, tensor in inputs.items()}

    def generate_pairs(
        self,
        sequences,
        candidate_labels,
        hypothesis_template
    ):
        if isinstance(sequences, str):
            sequences = [sequences]
        sequence_pairs = []
        for sequence in sequences:
            sequence_pairs.extend([[sequence, hypothesis_template.format(label)] for label in candidate_labels])
        return sequence_pairs

    def preprocess(
        self,
        sequences,
        candidate_labels,
        hypothesis_template="This example is {}.",
        padding=True,
        add_special_tokens=True,
        truncation="only_first",
        **kwargs

    ):
        sequence_pairs = self.generate_pairs(sequences, candidate_labels, hypothesis_template)
        inputs = self.tokenizer(
            sequence_pairs,
            add_special_tokens=add_special_tokens,
            return_tensors="pt",
            padding=padding,
            truncation=truncation,
        )
        return inputs

    def predict(self, query_params, payload):
        candidate_labels = payload["labels"]
        multi_label = payload["multi_label"]
        sequences = payload["data"]

        if isinstance(sequences, str):
            sequences = [sequences]
        inputs = self.preprocess(sequences, candidate_labels)

        with torch.no_grad():
            inputs = self.ensure_tensor_on_device(**inputs)
            outputs = self.model(**inputs)[0].cpu()

        num_sequences = len(sequences)
        reshaped_outputs = outputs.reshape((num_sequences, len(candidate_labels), -1))
        if len(candidate_labels) == 1:
            multi_label = True

        if not multi_label:
            # softmax the "entailment" logits over all candidate labels
            entail_logits = reshaped_outputs[..., self.entailment_id]
            scores = np.exp(entail_logits) / np.exp(entail_logits).sum(-1, keepdims=True)
        else:
            # softmax over the entailment vs. contradiction dim for each label independently
            entail_contr_logits = reshaped_outputs[..., [self.contradiction_id, self.entailment_id]]
            scores = np.exp(entail_contr_logits) / np.exp(entail_contr_logits).sum(-1, keepdims=True)
            scores = scores[..., 1]

        result = []
        for iseq in range(num_sequences):
            top_inds = list(reversed(scores[iseq].argsort()))
            result.append(
                {
                    "sequence": sequences if isinstance(sequences, str) else sequences[iseq],
                    "label_scores": {
                        candidate_labels[i]: scores[iseq][i].item() for i in top_inds
                    }
                }
            )

        if len(result) == 1:
            return result[0]
        return result
