import json
import os
import pickle

import argparse
import numpy as np
from tqdm import tqdm

from preprocess.vocab import Vocab


if __name__ == '__main__':
    arguments = argparse.ArgumentParser('MIMIC (v3) Note Tokens to Ids.')

    arguments.add_argument('--tokenized_fp', default='~/Desktop/mimic/NOTEEVENTS_tokenized_subsampled')
    arguments.add_argument('-debug', default=False, action='store_true')

    args = arguments.parse_args()

    # TODO remove
    args.debug = True

    args.tokenized_fp = os.path.expanduser(args.tokenized_fp)
    debug_str = '_mini' if args.debug else ''

    token_infile = '{}{}.json'.format(args.tokenized_fp, debug_str)
    print(token_infile)
    with open(token_infile, 'r') as fd:
        tokens = json.load(fd)

    # Load Vocabulary
    vocab_infile = '../preprocess/data/vocab{}.pk'.format(debug_str)
    with open(vocab_infile, 'rb') as fd:
        vocab = pickle.load(fd)

    ids = []
    N = len(tokens)
    for doc_idx in tqdm(range(N)):
        doc_tokens = tokens[doc_idx][1].split()
        doc_ids = vocab.get_ids(doc_tokens)
        ids += doc_ids
        ids += [0]  # Treating this as document boundary

    ids = ids[:-1]
    print('Saving {} tokens to disc'.format(len(ids)))
    out_fn = 'data/ids{}.npy'.format(debug_str)
    with open(out_fn, 'wb') as fd:
        np.save(fd, np.array(ids, dtype=int))