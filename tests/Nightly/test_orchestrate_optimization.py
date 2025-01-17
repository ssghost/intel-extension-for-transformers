# Copyright (c) 2024 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import torch
import numpy as np
import os
import shutil
import torch.utils.data as data
import unittest
from datasets import load_dataset, load_metric
from neural_compressor.config import (
    WeightPruningConfig,
    DistillationConfig,
    KnowledgeDistillationLossConfig,
    QuantizationAwareTrainingConfig,
)
from intel_extension_for_transformers.transformers import metrics
from intel_extension_for_transformers.transformers.trainer import NLPTrainer

from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    set_seed,
)

os.environ["WANDB_DISABLED"] = "true"

class TestOrchestrateOptimizations(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        set_seed(42)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            'distilbert-base-uncased'
        )
        self.teacher_model = AutoModelForSequenceClassification.from_pretrained(
            'distilbert-base-uncased-finetuned-sst-2-english'
        )
        raw_datasets = load_dataset("glue", "sst2")["validation"]
        tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased")
        def preprocess_function(examples):
            # Tokenize the texts
            args = (
                (examples['sentence'],)
            )
            result = tokenizer(*args, padding=True, max_length=64, truncation=True)
            return result
        raw_datasets = raw_datasets.map(
            preprocess_function, batched=True, load_from_cache_file=True
        )
        eval_dataset = raw_datasets.select(range(30))
        self.dataset = eval_dataset

    @classmethod
    def tearDownClass(self):
        shutil.rmtree('./tmp_trainer', ignore_errors=True)
        shutil.rmtree('./orchestrate_optimizations_model', ignore_errors=True)

    def test_fx_orchestrate_optimization(self):
        metric = load_metric("accuracy")
        def compute_metrics(p):
            preds = p.predictions
            preds = np.argmax(preds, axis=1)
            return metric.compute(predictions=preds, references=p.label_ids)

        self.trainer = NLPTrainer(
            model=copy.deepcopy(self.model),
            train_dataset=self.dataset,
            eval_dataset=self.dataset,
            compute_metrics=compute_metrics,
        )
        self.trainer.calib_dataloader = self.trainer.get_eval_dataloader()
        tune_metric = metrics.Metric(
            name="eval_accuracy", is_relative=True, criterion=0.5
        )
        self.trainer.metrics = tune_metric
        pruning_conf = WeightPruningConfig([{"start_step": 0, "end_step": 2}],
                                           target_sparsity=0.64,
                                           pruning_scope="local")
        distillation_criterion = KnowledgeDistillationLossConfig(loss_types=["CE", "KL"])
        distillation_conf = DistillationConfig(teacher_model=self.teacher_model, criterion=distillation_criterion)
        quantization_conf = QuantizationAwareTrainingConfig()
        conf_list = [pruning_conf, distillation_conf, quantization_conf]
        opt_model = self.trainer.orchestrate_optimizations(config_list=conf_list)
        self.assertTrue("quantize" in str(type(opt_model.classifier.module)))


if __name__ == "__main__":
    unittest.main()
