import pickle
import os
from shutil import rmtree
import sys
from time import sleep

import argparse
import numpy as np
import torch
from tqdm import tqdm

sys.path.insert(0, '/home/ga2530/ClinicalBayesianSkipGram/eval/')
sys.path.insert(0, '/home/ga2530/ClinicalBayesianSkipGram/preprocess/')
sys.path.insert(0, '/home/ga2530/ClinicalBayesianSkipGram/utils/')
from compute_sections import enumerate_metadata_ids_multi_bsg
from bsg_batcher import SkipGramBatchLoader
from bsg_model import BSG
from bsg_utils import restore_model, save_checkpoint
from model_utils import get_git_revision_hash, render_args
from evaluate import evaluate


if __name__ == '__main__':
    parser = argparse.ArgumentParser('Main script for Bayesian Skip Gram Model')

    # Functional Arguments
    parser.add_argument('-debug', action='store_true', default=False)
    parser.add_argument('--data_dir', default='../preprocess/data/')
    parser.add_argument('--experiment', default='default', help='Save path in weights/ for experiment.')
    parser.add_argument('--restore_experiment', default=None, help='Experiment name from which to restore.')

    # Training Hyperparameters
    parser.add_argument('--batch_size', default=1024, type=int)
    parser.add_argument('-combine_phrases', default=False, action='store_true')
    parser.add_argument('--epochs', default=4, type=int)
    parser.add_argument('--lr', default=0.001, type=float)
    parser.add_argument('--window', default=10, type=int)
    parser.add_argument('-cache_windows', default=False, action='store_true')

    # Model Hyperparameters
    parser.add_argument('--hidden_dim', default=64, type=int, help='hidden dimension for encoder')
    parser.add_argument('--input_dim', default=100, type=int, help='embedding dimemsions for encoder')
    parser.add_argument('--hinge_loss_margin', default=1.0, type=float, help='reconstruction margin')
    parser.add_argument('-multi_bsg', default=False, action='store_true')
    parser.add_argument('--multi_weights', default='0.7,0.2,0.1')
    parser.add_argument('-mask_p', default=None, type=float)

    args = parser.parse_args()
    args.git_hash = get_git_revision_hash()
    render_args(args)

    # Load Data
    debug_str = '_mini' if args.debug else ''
    phrase_str = '_phrase' if args.combine_phrases else ''

    ids_infile = os.path.join(args.data_dir, 'ids{}{}.npy'.format(debug_str, phrase_str))
    print('Loading data from {}...'.format(ids_infile))
    with open(ids_infile, 'rb') as fd:
        ids = np.load(fd)

    # Load Vocabulary
    vocab_infile = os.path.join(args.data_dir, 'vocab{}{}.pk'.format(debug_str, phrase_str))
    print('Loading vocabulary from {}...'.format(vocab_infile))
    with open(vocab_infile, 'rb') as fd:
        vocab = pickle.load(fd)
    print('Loaded vocabulary of size={}...'.format(vocab.section_start_vocab_id))

    print('Collecting metadata information')
    assert vocab.section_start_vocab_id <= vocab.category_start_vocab_id
    sec_id_range = np.arange(vocab.section_start_vocab_id, vocab.category_start_vocab_id)
    cat_id_range = np.arange(vocab.category_start_vocab_id, vocab.size())

    sec_pos_idxs = np.where(np.isin(ids, sec_id_range))[0]
    cat_pos_idxs = np.where(np.isin(ids, cat_id_range))[0]

    sec_ids, cat_ids = enumerate_metadata_ids_multi_bsg(ids, sec_pos_idxs, cat_pos_idxs)

    print('Snippet from beginning of data...')
    for ct, (sid, cid, tid) in enumerate(zip(sec_ids, cat_ids, ids)):
        print('\t', vocab.get_tokens([sid, cid, tid]))
        if ct >= 10:
            break

    all_metadata_pos_idxs = np.concatenate([sec_pos_idxs, cat_pos_idxs])
    # Demarcates boundary tokens
    ids[all_metadata_pos_idxs] = -1

    device_str = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Training on {}...'.format(device_str))

    batcher = SkipGramBatchLoader(len(ids), all_metadata_pos_idxs, batch_size=args.batch_size)

    model = BSG(args, vocab.size()).to(device_str)
    if args.restore_experiment is not None:
        prev_args, model, vocab, optimizer_state = restore_model(args.restore_experiment)

    # Instantiate Adam optimizer
    trainable_params = filter(lambda x: x.requires_grad, model.parameters())
    optimizer = torch.optim.Adam(trainable_params, lr=args.lr)
    if args.restore_experiment is not None:
        optimizer.load_state_dict(optimizer_state)

    # Create model experiments directory or clear if it already exists
    weights_dir = os.path.join('weights', args.experiment)
    if os.path.exists(weights_dir):
        print('Clearing out previous weights in {}'.format(weights_dir))
        rmtree(weights_dir)
    os.mkdir(weights_dir)

    # Make sure it's calculating gradients
    model.train()  # just sets .requires_grad = True
    for epoch in range(1, args.epochs + 1):
        sleep(0.1)  # Make sure logging is synchronous with tqdm progress bar
        print('Starting Epoch={}'.format(epoch))
        batcher.reset()
        num_batches = batcher.num_batches()
        epoch_joint_loss, epoch_kl_loss, epoch_recon_loss = 0.0, 0.0, 0.0
        for _ in tqdm(range(num_batches)):
            # Reset gradients
            optimizer.zero_grad()

            batch_ids = batcher.next(ids, sec_ids, cat_ids, vocab, args.window)
            batch_ids = list(map(lambda x: torch.LongTensor(x).to(device_str), batch_ids))

            kl_loss, recon_loss = kl_loss, recon_loss = model(*batch_ids)
            joint_loss = kl_loss + recon_loss
            joint_loss.backward()  # backpropagate loss

            epoch_kl_loss += kl_loss.item()
            epoch_recon_loss += recon_loss.item()
            epoch_joint_loss += joint_loss.item()
            optimizer.step()
        epoch_joint_loss /= float(batcher.num_batches())
        epoch_kl_loss /= float(batcher.num_batches())
        epoch_recon_loss /= float(batcher.num_batches())
        sleep(0.1)
        print('Epoch={}. Joint loss={}.  KL Loss={}. Reconstruction Loss={}'.format(
            epoch, epoch_joint_loss, epoch_kl_loss, epoch_recon_loss))
        assert not batcher.has_next()

        # Serializing everything from model weights and optimizer state, to to loss function and arguments
        losses_dict = {'losses': {'joint': epoch_joint_loss, 'kl': epoch_kl_loss, 'recon': epoch_recon_loss}}
        checkpoint_fp = os.path.join(weights_dir, 'checkpoint_{}.pth'.format(epoch))
        save_checkpoint(args, model, optimizer, vocab, losses_dict, checkpoint_fp=checkpoint_fp)

    # Run evaluations
    evaluate(args)
