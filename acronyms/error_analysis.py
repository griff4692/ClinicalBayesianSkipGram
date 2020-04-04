from collections import defaultdict
import json
import os
import sys

import pandas as pd
from pycm import ConfusionMatrix
from sklearn.metrics import classification_report
import torch
from tqdm import tqdm


home_dir = os.path.expanduser('~/LMC/')
sys.path.insert(0, os.path.join(home_dir, 'utils'))
from acronym_utils import process_batch
from model_utils import tensor_to_np


def _analyze_batch(batch_data, sf_lf_map, pred_lf_idxs, correct_str, errors_str, sf_confusion, id_map, rel_weights):
    for batch_idx, (row_idx, row) in enumerate(batch_data.iterrows()):
        row = row.to_dict()
        sf = row['sf']
        lf_map = sf_lf_map[sf]
        target_lf = row['target_lf']
        target_lf_idx = row['target_lf_idx']
        pred_lf_idx = pred_lf_idxs[batch_idx]
        pred_lf = lf_map[pred_lf_idx]
        rel_weight = rel_weights[batch_idx] if rel_weights is not None else None
        example_str = _render_example(sf, target_lf, lf_map[target_lf_idx], pred_lf,
                                      row['trimmed_tokens'], row['tokenized_context'], row['row_idx'], rel_weight)
        if target_lf_idx == pred_lf_idx:
            id_map['correct'].append(row['row_idx'])
            correct_str[sf] += example_str
        else:
            id_map['error'].append(row['row_idx'])
            errors_str[sf] += example_str
        sf_confusion[sf][0].append(target_lf_idx)
        sf_confusion[sf][1].append(pred_lf_idx)


def _analyze_stats(results_dir, sf_lf_map, correct_str, errors_str, sf_confusion, id_map, experiment=None):
    """
    :param results_dir: directory in which to write results
    :param sf_lf_map: Map of SFs to candidate LFs (i.e. AA --> {amino acids, alcoholics anonymous, etc.})
    :param correct_str: String containing prettified versions of all correctly predicted examples
    :param errors_str: String containing prettified versions of all incorrectly predicted examples
    :param sf_confusion: dictionary of confusion matrices for SFs
    :param id_map: Dictionary with keys 'correct' and 'error'.  Values are example ids.  For generating inter-model
    confusion matries
    :param experiment: name of experiment in which to write results in side of results_dir/
    :return:
    """
    correct_fp = os.path.join(results_dir, 'correct.txt')
    reports_fp = os.path.join(results_dir, 'reports.txt')
    errors_fp = os.path.join(results_dir, 'errors.txt')
    summary_fp = os.path.join(results_dir, 'summary.csv')
    id_fp = os.path.join(results_dir, 'error_tracker.json')
    df = defaultdict(list)
    cols = [
        'sf',
        'experiment',
        'support',
        'num_targets',
        'micro_precision',
        'micro_recall',
        'micro_f1',
        'macro_precision',
        'macro_recall',
        'macro_f1',
        'weighted_precision',
        'weighted_recall',
        'weighted_f1',
    ]
    with open(id_fp, 'w') as fd:
        json.dump(id_map, fd)
    reports = []
    with open(correct_fp, 'w') as fd:
        for k in sorted(correct_str.keys()):
            fd.write(correct_str[k])
    with open(errors_fp, 'w') as fd:
        for k in sorted(errors_str.keys()):
            fd.write(errors_str[k])
    for sf in sf_confusion:
        labels = sf_lf_map[sf]
        labels_trunc = list(map(lambda x: x.split(';')[0], labels))
        y_true = sf_confusion[sf][0]
        y_pred = sf_confusion[sf][1]
        sf_results = classification_report(y_true, y_pred, labels=list(range(len(labels_trunc))),
                                           target_names=labels_trunc, output_dict=True)
        report = classification_report(y_true, y_pred, labels=list(range(len(labels_trunc))),
                                       target_names=labels_trunc)

        macro_nonzero = defaultdict(float)
        num_nonzero = 0
        for lf in labels_trunc:
            d = sf_results[lf]
            if d['support'] > 0:
                macro_nonzero['precision'] += d['precision']
                macro_nonzero['recall'] += d['recall']
                macro_nonzero['f1-score'] += d['f1-score']
                num_nonzero += 1

        for suffix in ['precision', 'recall', 'f1-score']:
            sf_results['macro avg'][suffix] = macro_nonzero[suffix] / float(num_nonzero)
        reports.append(report)
        reports.append('\n\n')
        metrics = ['micro avg', 'macro avg', 'weighted avg']
        for metric in metrics:
            if metric in sf_results:
                for k, v in sf_results[metric].items():
                    if not k == 'support':
                        metric_key = '{}_{}'.format(metric.split(' ')[0], k.split('-')[0])
                        df[metric_key].append(v)
            else:
                for suffix in ['precision', 'recall', 'f1']:
                    df['{}_{}'.format(metric.split(' ')[0], suffix)].append(None)
        df['sf'].append(sf)
        df['num_targets'].append(len(labels_trunc))
        df['support'].append(sf_results['weighted avg']['support'])
        try:
            cm = ConfusionMatrix(actual_vector=y_true, predict_vector=y_pred)
            label_idx_to_str = dict()
            for idx in cm.classes:
                label_idx_to_str[idx] = labels_trunc[int(idx)]
            cm.relabel(mapping=label_idx_to_str)
            cm_outpath = os.path.join(results_dir, 'confusion', sf)
            cm.save_html(cm_outpath)
        except:
            print('Only 1 target class for test set SF={}'.format(sf))

    df['experiment'] = [experiment] * len(df['sf'])
    summary_df = pd.DataFrame(df, columns=cols)
    summary_df.to_csv(summary_fp, index=False)
    with open(reports_fp, 'w') as fd:
        for report in reports:
            fd.write(report)

    suffixes = ['precision', 'recall', 'f1']
    types = ['weighted', 'macro']
    agg_metrics = {}
    for t in types:
        for suffix in suffixes:
            key = '{}_{}'.format(t, suffix)
            avg_val = summary_df[key].mean()
            print('Global {} --> {}'.format(key, avg_val))
            agg_metrics[key] = avg_val

    num_targets = summary_df['num_targets'].unique().tolist()
    print('Num Targets, Macro F1, Weighted F1')
    for t in sorted(num_targets):
        avg_macro_f1 = summary_df[summary_df['num_targets'] == t]['macro_f1'].mean()
        avg_weighted_f1 = summary_df[summary_df['num_targets'] == t]['weighted_f1'].mean()
        print('{},{},{}'.format(t, avg_macro_f1, avg_weighted_f1))

    return agg_metrics


def _render_example(sf, target_lf, converted_target_lf, pred_lf, context_window, full_context, row_idx, rel_weight):
    """
    :param sf: Short Form
    :param target_lf: Gold Standard Long Form expansion for SF
    :param converted_target_lf: Canonical Format for LF
    :param pred_lf: Predicted LF
    :param context_window: Words surrounding SF passed to the model
    :param full_context: The entire passage provided by dataset (context_window is a subset of this)
    :param row_idx: Example ID representing it's place in the test dataframe
    :param rel_weight: For LMC models, this represents the weight placed on the metadata vis-a-vis token in encoder
    :return: printable string for this example
    """
    str = 'SF={}.\nTarget LF={} ({}).\nPredicted LF={}.\n'.format(sf, target_lf, converted_target_lf, pred_lf)
    str += 'Example ID={}\n'.format(row_idx)
    if rel_weight is not None:
        str += 'Metadata Weight={}.  Context Word Weight={}\n'.format(rel_weight[0], rel_weight[1])
    str += 'Context Window: {}\n'.format(context_window)
    str += 'Full Context: {}\n'.format(full_context)
    str += '\n\n'
    return str


def analyze(args, test_batcher, model, sf_lf_map, loss_func, token_vocab, metadata_vocab, sf_tokenized_lf_map,
            token_metadata_counts, results_dir=None):
    """
    :param args: ArgParse instance
    :param test_batcher: AcronymBatcherLoader instance
    :param model: AcronymExpander instance
    :param sf_lf_map: Short form to LF mappings
    :param loss_func: PyTorch CrossEntropyLoss instance
    :param vocab: Vocab instance storing tokens and corresponding token ids
    :param sf_tokenized_lf_map: dictionary of SF --> tokenized LFs
    :param results_dir: where to write the results files
    :return: None but writes analysis files into results_dir
    """
    test_batcher.reset(shuffle=False)
    model.eval()

    sf_confusion = defaultdict(lambda: ([], []))
    id_map = {'correct': [], 'error': []}
    errors_str, correct_str = defaultdict(str), defaultdict(str)
    for _ in range(test_batcher.num_batches()):
        with torch.no_grad():
            _, _, _, batch_scores, rel_weights = process_batch(
                args, test_batcher, model, loss_func, token_vocab, metadata_vocab, sf_lf_map, sf_tokenized_lf_map,
                token_metadata_counts)
        batch_data = test_batcher.get_prev_batch()
        pred_lf_idxs = tensor_to_np(torch.argmax(batch_scores, 1))
        if rel_weights is not None:
            rel_weights = tensor_to_np(rel_weights)
        _analyze_batch(batch_data, sf_lf_map, pred_lf_idxs, correct_str, errors_str, sf_confusion, id_map, rel_weights)

    return _analyze_stats(results_dir, sf_lf_map, correct_str, errors_str, sf_confusion, id_map, experiment=args.experiment)


def elmo_analyze(test_batcher, model, sf_lf_map, vocab, sf_tokenized_lf_map, indexer, results_dir=None):
    """
    :param args: argparse.ArgumentParser instance
    :param test_batcher: AcronymBatcherLoader instance
    :param model: AcronymExpander instance
    :param sf_lf_map: Short form to LF mappings
    :param vocab: Vocabulary AllenNLP instance storing tokens and corresponding token ids
    :param indexer: AllenNLP token to id indexer
    :param results_dir: where to write the results files
    :return: None but writes a confusion matrix analysis file into results_dir
    """
    test_batcher.reset(shuffle=False)
    model.eval()

    sf_confusion = defaultdict(lambda: ([], []))
    id_map = {'correct': [], 'error': []}
    errors_str, correct_str = defaultdict(str), defaultdict(str)

    for _ in tqdm(range(test_batcher.num_batches())):
        batch_input, num_outputs = test_batcher.elmo_next(vocab, indexer, sf_tokenized_lf_map)
        batch_input = list(map(lambda x: torch.LongTensor(x).clamp_min_(0).to('cuda'), batch_input))
        with torch.no_grad():
            scores, target = model(*batch_input + [num_outputs])
        pred_lf_idxs = tensor_to_np(torch.argmax(scores, 1))
        batch_data = test_batcher.get_prev_batch()
        _analyze_batch(batch_data, sf_lf_map, pred_lf_idxs, correct_str, errors_str, sf_confusion, id_map, None)
    return _analyze_stats(results_dir, sf_lf_map, correct_str, errors_str, sf_confusion, id_map, experiment='elmo')


def render_test_statistics(df, sf_lf_map):
    """
    :param df: Test dataframe
    :param sf_lf_map: Map of SFs to candidate LFs (i.e. AA --> {amino acids, alcoholics anonymous, etc.})
    :return: None

    Renders basic statistics about test set such as performance from random guessing and the makeup of the dominant
    class.
    """
    N = df.shape[0]
    sfs = df['sf'].unique().tolist()
    sf_counts = df['sf'].value_counts()

    dominant_counts = 0
    expected_random_accuracy = 0.0
    for sf in sfs:
        dominant_counts += df[df['sf'] == sf]['target_lf_idx'].value_counts().max()
        expected_random_accuracy += sf_counts[sf] / float(len(sf_lf_map[sf]))

    print('Accuracy from choosing dominant class={}'.format(dominant_counts / float(N)))
    print('Expected random accuracy={}'.format(expected_random_accuracy / float(N)))
