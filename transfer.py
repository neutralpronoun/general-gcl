import argparse
import concurrent.futures
import glob
import logging
import os
import random
from datetime import datetime

import matplotlib as mpl
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import torch
import yaml
from bokeh.models import HoverTool, BoxZoomTool, ResetTool, Range1d, UndoTool, WheelZoomTool, PanTool
from bokeh.plotting import figure, output_file, show, ColumnDataSource
from hdbscan import HDBSCAN
from ogb.graphproppred import PygGraphPropPredDataset

from umap import UMAP
from sklearn.decomposition import TruncatedSVD
from sklearn.manifold import SpectralEmbedding, Isomap

from torch_geometric.data import DataLoader
from torch_geometric.transforms import Compose
from tqdm import tqdm

from utils import better_to_nx
from datasets.loaders import get_train_loader, get_val_loaders, get_test_loaders

import wandb
from datasets.community_dataset import CommunityDataset
from datasets.cora_dataset import CoraDataset
from datasets.ego_dataset import EgoDataset
from datasets.facebook_dataset import FacebookDataset
from datasets.from_ogb_dataset import FromOGBDataset
from datasets.neural_dataset import NeuralDataset
from datasets.random_dataset import RandomDataset
from datasets.road_dataset import RoadDataset
from datasets.tree_dataset import TreeDataset
from datasets.lattice_dataset import LatticeDataset

from sklearn.metrics import f1_score, roc_auc_score, mean_squared_error

from unsupervised.embedding_evaluation import GeneralEmbeddingEvaluation, TargetEvaluation
from unsupervised.encoder import MoleculeEncoder
from unsupervised.learning import GInfoMinMax
from unsupervised.utils import initialize_edge_weight
from unsupervised.view_learner import ViewLearner
from unsupervised.encoder import TransferModel

from torch.nn import MSELoss, BCELoss, Softmax, Sigmoid


def warn(*args, **kwargs):
    pass
import warnings
warnings.warn = warn

def setup_wandb(cfg):
    """
    Uses a config dictionary to initialise wandb to track sampling.
    Requires a wandb account, https://wandb.ai/

    params: cfg: argparse Namespace

    returns:
    param: cfg: same config
    """

    kwargs = {'name': f"{cfg.dataset}-" + datetime.now().strftime("%m-%d-%Y-%H-%M-%S"), 'project': f'gcl_{cfg.dataset}', 'config': cfg,
              'settings': wandb.Settings(_disable_stats=False), 'reinit': True, 'entity':'hierarchical-diffusion', 'mode':'offline'}
    wandb.init(**kwargs)
    wandb.save('*.txt')

    wandb.log({"Type":"Sampling"})

    return cfg

def setup_seed(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    np.random.seed(seed)
    random.seed(seed)




def wandb_cfg_to_actual_cfg(original_cfg, wandb_cfg):
    """
    Retrive wandb config from saved file
    Args:
        original_cfg: the config from this run
        wandb_cfg: the saved config from the training run

    Returns:
        a config with values updated to those from the saved training run
    """
    original_keys = list(vars(original_cfg).keys())
    wandb_keys = list(wandb_cfg.keys())

    for key in original_keys:
        if key not in wandb_keys:
            continue

        vars(original_cfg)[key] = wandb_cfg[key]['value']

    return original_cfg


def tidy_labels(labels):
    if type(labels[0]) is not list:
        if np.sum(labels) == np.sum(np.array(labels).astype(int)):
            labels = np.array(labels).astype(int)
        else:
            labels = np.array(labels)
        return labels

    # Could be lists of floats
    elif type(labels[0][0]) is float:
        return np.array(labels)

    # Possibility of one-hot labels
    elif np.sum(labels[0][0]) == 1 and type(labels[0][0]) is int:

        new_labels = []
        for label in labels:
            new_labels.append(np.argmax(label))

        return np.array(new_labels)

    else:
        return np.array(labels)

def get_task_type(loader, name):
    val_targets = []
    for i_batch, batch in enumerate(loader):
        if batch.y is None or name == "ogbg-molpcba" or name == "blank":
            task = "empty"
            n_samples = 0
            return task

        else:
            selected_y = batch.y
            if type(selected_y) is list:
                selected_y = torch.Tensor(selected_y)

            if selected_y.dim() > 1:
                selected_y = [selected_y[i, :].cpu().numpy().tolist() for i in range(selected_y.shape[0])]
            else:
                selected_y = selected_y.cpu().numpy().tolist()

            val_targets += selected_y

        break

    val_targets = tidy_labels(val_targets).flatten()
    if type(val_targets[0]) is int or type(val_targets[0]) is np.int64:
        task = "classification"
    else:
        task = "regression"

    return task

def evaluate_model(model, test_loader, score_fn, out_fn, loss_fn, task):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.eval()
    with torch.no_grad():
        ys, y_preds = [], []
        for batch in test_loader:
            batch.to(device)
            y_pred, _ = model(batch.batch, batch.x, batch.edge_index, batch.edge_attr, None)
            y = batch.y

            if type(y) is list:
                y = torch.tensor(y).to(device)

            if task == "classification":
                y_pred = out_fn(y_pred).flatten()
                if y.dim() > 1:
                    y = y[:, 0]
                y = y.to(y_pred.dtype)

            y_pred = y_pred.cpu().numpy().tolist()
            y = y.cpu().numpy().tolist()

            ys += y
            y_preds += y_pred
    model.train()
    try:
        return score_fn(ys, y_preds, squared=False), loss_fn(torch.tensor(ys), torch.tensor(y_preds))
    except:
        return score_fn(ys, y_preds), loss_fn(torch.tensor(ys), torch.tensor(y_preds))


def fine_tune(model, checkpoint_path, val_loader, test_loader, name = "blank", n_epochs = 50):
    # At the moment this is rigid to single-value predictions

    device = "cuda" if torch.cuda.is_available() else "cpu"
    task = get_task_type(val_loader, name)

    model_name = checkpoint_path.split("/")[-1].split(".")[0]
    if model_name not in os.listdir("outputs"):
        os.mkdir(f"outputs/{model_name}")

    if task == "empty":
        return

    if task == "classification":
        loss_fn = BCELoss()
        score_fn = roc_auc_score
    else:
        loss_fn = MSELoss()
        score_fn = mean_squared_error


    if checkpoint_path != "untrained":
        model_dict = torch.load(checkpoint_path, map_location=torch.device('cpu'))
        model.load_state_dict(model_dict['encoder_state_dict'], strict=False)
    model.to(device)

    out_fn = Sigmoid()
    model_optimizer = torch.optim.Adam(model.parameters(), lr=args.model_lr)
    print(f"\nName: {name}, State: {model_name}")
    pbar = tqdm(range(n_epochs), leave=False)
    best_val_loss, best_epoch = 1.e9, 0
    train_losses, val_losses = [], []
    for i_epoch in pbar:
        model.train()
        # ys, y_preds = [], []
        for i_batch, batch in enumerate(val_loader):
            model.zero_grad()
            # set up
            batch = batch.to(device)
            y = batch.y

            if type(y) is list:
                y = torch.tensor(y).to(device)

            y_pred, _ = model(batch.batch, batch.x, batch.edge_index, batch.edge_attr, None)
            if task == "classification":
                y_pred = out_fn(y_pred).flatten()
                if y.dim() > 1:
                    y = y[:, 0]
            y = y.to(y_pred.dtype)

            model_loss = loss_fn(y_pred, y)
            train_losses.append(model_loss.item())

            model_loss.backward()
            model_optimizer.step()
        if i_epoch == 0:
            print(f"Untrained Score: {evaluate_model(model, test_loader, score_fn, out_fn, loss_fn,  task)[0]}")
        pbar.set_description(str(model_loss.item())[:6])
        _, val_loss = evaluate_model(model, test_loader, score_fn, out_fn, loss_fn, task)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = i_epoch
            final_score = evaluate_model(model, test_loader, score_fn, out_fn, loss_fn, task)[0]

        val_losses.append(val_loss.item())

    print(f"Final Score: {final_score}")

    fig, ax = plt.subplots(figsize=(6,4))
    ax.plot(np.linspace(start = 0, stop = n_epochs, num=len(train_losses)), train_losses, label = "Train")
    ax.plot(np.linspace(start = 0, stop = n_epochs, num=len(val_losses)), val_losses, label="Val")
    ax.legend(shadow=True)
    plt.savefig(f"outputs/{model_name}/{name}.png")
    plt.close()

    return train_losses, val_losses, final_score, best_epoch, best_val_loss


def run(args):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    logging.info("Using Device: %s" % device)
    logging.info("Seed: %d" % args.seed)
    logging.info(args)
    # setup_seed(args.seed)

    num = args.num
    checkpoint = args.checkpoint
    evaluation_node_features = args.node_features

    if checkpoint == "latest":
        checkpoint_root = "wandb/latest-run/files"
        checkpoints = glob.glob(f"{checkpoint_root}/Checkpoint-*.pt")
        print(checkpoints)
        epochs = np.array([cp.split('-')[0] for cp in checkpoints])
        checkpoint_ind = np.argsort(epochs[::-1])[0]

        checkpoint_path = f"wandb/latest-run/files/{checkpoints[checkpoint_ind]}"

    elif checkpoint == "untrained":
        checkpoint_path = "untrained"

    else:
        checkpoint_path = f"outputs/{checkpoint}"
        cfg_name = checkpoint.split('.')[0] + ".yaml"
        config_path = f"outputs/{cfg_name}"

        with open(config_path, 'r') as stream:
            try:
                # Converts yaml document to python object
                wandb_cfg = yaml.safe_load(stream)

                # Printing dictionary
                print(wandb_cfg)
            except yaml.YAMLError as e:
                print(e)

        args = wandb_cfg_to_actual_cfg(args, wandb_cfg)

    # Get datasets
    my_transforms = Compose([initialize_edge_weight])

    test_loaders, names = get_test_loaders(args.batch_size, my_transforms, num=num)
    val_loaders, names = get_val_loaders(args.batch_size, my_transforms, num=2*num)
    model_name = checkpoint_path.split("/")[-1].split(".")[0]

    for i in range(len(val_loaders)):
        val_loader = val_loaders[i]
        test_loader = test_loaders[i]
        name = names[i]

        if name not in os.listdir("outputs"):
            os.mkdir(f"outputs/{name}")

        model = TransferModel(
            MoleculeEncoder(emb_dim=args.emb_dim, num_gc_layers=args.num_gc_layers, drop_ratio=args.drop_ratio,
                            pooling_type=args.pooling_type),
            proj_hidden_dim=args.emb_dim, output_dim=1, features=evaluation_node_features).to(device)

        # try:
        num_epochs = 50
        pretrain_train_losses, pretrain_val_losses, pretrain_val_score, pretrain_best_epoch, pretrain_best_val_loss = fine_tune(model,
                                                                                                                                checkpoint_path,
                                                                                                                                val_loader,
                                                                                                                                test_loader,
                                                                                                                                name = name,
                                                                                                                                n_epochs=num_epochs)

        model = TransferModel(
            MoleculeEncoder(emb_dim=args.emb_dim, num_gc_layers=args.num_gc_layers, drop_ratio=args.drop_ratio,
                            pooling_type=args.pooling_type),
            proj_hidden_dim=args.emb_dim, output_dim=1, features=evaluation_node_features).to(device)

        untrain_train_losses, untrain_val_losses, untrain_val_score, untrain_best_epoch,untrain_best_val_loss = fine_tune(model,
                                                                                                                          "untrained",
                                                                                                                          val_loader,
                                                                                                                          test_loader,
                                                                                                                          name = name,
                                                                                                                          n_epochs=num_epochs)

        untrain_val_score = str(untrain_val_score)[:6]
        pretrain_val_score = str(pretrain_val_score)[:6]

        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(np.linspace(start = 0, stop = num_epochs, num=len(pretrain_val_losses)), pretrain_val_losses,
                label=f"Pre-Trained (MuD), Best score: {pretrain_val_score}, Best epoch: {pretrain_best_epoch}",
                c = "orange")

        ax.plot(np.linspace(start = 0, stop = num_epochs, num=len(pretrain_val_losses)), untrain_val_losses,
                label=f"From Scratch, Best score: {untrain_val_score}, Best epoch: {untrain_best_epoch}",
                c = "blue")

        ax.axhline(pretrain_best_val_loss, label = "Pre-train lowest val loss", c = "orange")
        ax.axhline(untrain_best_val_loss, label="Un-train lowest val loss", c = "blue")

        ax.legend(shadow=True)
        ax.set_xlabel("Batch")
        ax.set_ylabel("Validation Loss")

        # if max(pretrain_val_losses + untrain_val_losses) > 2000:
        ax.set_yscale('log')

        features_string_tag = "feats" if evaluation_node_features else "no-feats"
        plt.savefig(f"outputs/{name}/{model_name}-{features_string_tag}.png")
        plt.close()




def arg_parse():
    parser = argparse.ArgumentParser(description='AD-GCL ogbg-mol*')

    parser.add_argument('--dataset', type=str, default='ogbg-molesol',
                        help='Dataset')
    parser.add_argument('--model_lr', type=float, default=0.001,
                        help='Model Learning rate.')
    parser.add_argument('--view_lr', type=float, default=0.001,
                        help='View Learning rate.')
    parser.add_argument('--num_gc_layers', type=int, default=5,
                        help='Number of GNN layers before pooling')
    parser.add_argument('--pooling_type', type=str, default='standard',
                        help='GNN Pooling Type Standard/Layerwise')
    parser.add_argument('--emb_dim', type=int, default=300,
                        help='embedding dimension')
    parser.add_argument('--mlp_edge_model_dim', type=int, default=32,
                        help='embedding dimension')
    parser.add_argument('--batch_size', type=int, default=256,
                        help='batch size')
    parser.add_argument('--drop_ratio', type=float, default=0.2,
                        help='Dropout Ratio / Probability')
    parser.add_argument('--epochs', type=int, default=5000,
                        help='Train Epochs')
    parser.add_argument('--reg_lambda', type=float, default=5.0, help='View Learner Edge Perturb Regularization Strength')

    parser.add_argument('--seed', type=int, default=0)

    parser.add_argument('--num', type=int, default=1000,
                        help='Number of points included in each dataset')

    parser.add_argument('--redo_views', type=bool, default=False,
                        help='Whether to re-vis views')

    parser.add_argument('--checkpoint', type=str, default="latest", help='Either the name of the trained model checkpoint in ./outputs/, or latest for the most recent trained model in ./wandb/latest-run/files')

    parser.add_argument(
        '-f',
        '--node-features',
        action='store_true',
        help='Whether to include node features (labels) in evaluation',
    )

    return parser.parse_args()


if __name__ == '__main__':

    args = arg_parse()
    run(args)

