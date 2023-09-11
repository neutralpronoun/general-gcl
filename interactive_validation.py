import argparse
import logging
import random

import wandb
from datetime import datetime
from tqdm import tqdm
import os

import matplotlib as mpl

from sklearnex import patch_sklearn
patch_sklearn()

import numpy as np
import torch
from ogb.graphproppred import Evaluator
from ogb.graphproppred import PygGraphPropPredDataset, GraphPropPredDataset
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.neural_network import MLPRegressor, MLPClassifier
from torch_geometric.data import DataLoader, Data
from torch_geometric.transforms import Compose
from torch_scatter import scatter
import matplotlib.pyplot as plt

import networkx as nx
from datasets.facebook_dataset import get_fb_dataset, FacebookDataset
from datasets.ego_dataset import get_deezer, EgoDataset
from datasets.community_dataset import get_community_dataset, CommunityDataset
from datasets.cora_dataset import get_cora_dataset, CoraDataset
from datasets.random_dataset import RandomDataset
from datasets.from_ogb_dataset import FromOGBDataset

from unsupervised.embedding_evaluation import EmbeddingEvaluation, GeneralEmbeddingEvaluation, DummyEmbeddingEvaluation
from unsupervised.encoder import MoleculeEncoder
from unsupervised.learning import GInfoMinMax
from unsupervised.utils import initialize_edge_weight
from unsupervised.view_learner import ViewLearner

from umap import UMAP
from sklearn.decomposition import PCA, FastICA, TruncatedSVD
import glob
from bokeh.plotting import figure, output_file, show, ColumnDataSource
from bokeh.models import HoverTool, BoxZoomTool, ResetTool, Range1d
from bokeh.palettes import d3, Spectral
import bokeh.models as bmo



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
    # config_dict = omegaconf.OmegaConf.to_container(cfg, resolve=True, throw_on_missing=True)

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


def vis_from_pyg(data, filename = None):
    edges = data.edge_index.T.cpu().numpy()
    labels = data.x[:,0].cpu().numpy()

    g = nx.Graph()
    g.add_edges_from(edges)

    # dropped_nodes = np.ones(labels.shape[0]).astype(bool)
    for ilabel in range(labels.shape[0]):
        if ilabel not in np.unique(edges):
            g.add_node(ilabel)
    # labels = labels[dropped_nodes]

    fig, ax = plt.subplots(figsize = (6,6))

    pos = nx.kamada_kawai_layout(g)

    nx.draw_networkx_edges(g, pos = pos, ax = ax)
    nx.draw_networkx_nodes(g, pos = pos, node_color=labels, cmap="tab20",
                           vmin = 0, vmax = 20, ax = ax)

    ax.axis('off')

    plt.tight_layout()
    if filename is None:
        plt.show()
    else:
        plt.savefig(filename)
        plt.close()

def get_big_dataset(dataset, batch_size, transforms, num_social = 100000):
    names = ["ogbg-molclintox"]
    datasets = [PygGraphPropPredDataset(name=name, root='./original_datasets/', transform=transforms) for name in names]

    split_idx = [data.get_idx_split() for data in datasets]

    datasets = [data[split_idx[i]["train"]] for i, data in enumerate(datasets)]

    datasets = [FromOGBDataset(os.getcwd()+'/original_datasets/'+names[i], data) for i, data in enumerate(datasets)]

    combined = FromOGBDataset(os.getcwd()+'/original_datasets/'+'ogbg-molesol', dataset)

    for data in datasets:
        combined += data
        #     item.y = torch.Tensor([[0,0]])
        #     print(item.y)

    for item in combined:
        if item.y is not None:
            print(item)

    social_datasets = [transforms(FacebookDataset(os.getcwd()+'/original_datasets/'+'facebook_large', num=num_social)),
                       transforms(EgoDataset(os.getcwd()+'/original_datasets/'+'twitch_egos', num=num_social, stage="train")),
                       transforms(CoraDataset(os.getcwd()+'/original_datasets/'+'cora', num=num_social))]

    for data in social_datasets:
        combined += data
            # item.y = torch.Tensor([[0,0]])




    return DataLoader(combined, batch_size=batch_size, shuffle=True), "Dummy"

    #
    # datasets = datasets + [get_fb_dataset(num = num_social),
    #                   get_deezer(num=2 * num_social),
    #                   get_cora_dataset(batch_size, num=num_social)]#,
    #                   # get_community_dataset(batch_size, num = num_social),
    #                   # get_random_dataset(batch_size, num = num_social)
    #                   # ]

    # large_dataset =

    # return [DataLoader(data, batch_size=batch_size) for data in datasets + [dataset]] +  social_loaders,\
    #        names +  ["Molesol (target)", "Facebook", "Egos", "Cora"]#, "Communities", "Random"]

    # out = torch.utils.data.ConcatDataset([datasets])
    #
    # return out

def get_val_loaders(dataset, batch_size, transforms, num_social = 15000):
    names = ["ogbg-molclintox", "ogbg-molpcba"]


    # datasets = [PygGraphPropPredDataset(name=name, root='./original_datasets/', transform=transforms) for name in names]

    # split_idx = [data.get_idx_split() for data in datasets]

    # datasets = [data[split_idx[i]["train"]] for i, data in enumerate(datasets)]

    social_datasets = [transforms(FacebookDataset(os.getcwd()+'/original_datasets/'+'facebook_large', stage = "val", num=num_social)),
                       transforms(EgoDataset(os.getcwd()+'/original_datasets/'+'twitch_egos', stage = "val", num=num_social)),
                       transforms(CoraDataset(os.getcwd()+'/original_datasets/'+'cora', stage = "val", num=num_social)),
                       transforms(RandomDataset(os.getcwd()+'/original_datasets/'+'random', stage = "val", num=num_social)),
                       transforms(CommunityDataset(os.getcwd()+'/original_datasets/'+'community', stage = "val", num=num_social))]



    datasets = [PygGraphPropPredDataset(name=name, root='./original_datasets/', transform=transforms) for name in names]
    split_idx = [data.get_idx_split() for data in datasets]

    datasets = [data[split_idx[i]["valid"]] for i, data in enumerate(datasets)]

    dataset_lengths = [len(data) for data in datasets]

    for i, data in enumerate(datasets):
        if dataset_lengths[i] > num_social:
            datasets[i] = data[:num_social]

    datasets = [FromOGBDataset(os.getcwd()+'/original_datasets/'+names[i], data, stage = "val", num=num_social) for i, data in enumerate(datasets)]

    datasets = datasets + [FromOGBDataset(os.getcwd()+'/original_datasets/'+'ogbg-molesol', dataset, stage = "val")]


    # combined = dataset

    # for data in datasets:
    #     combined += data



    all_datasets = datasets + social_datasets

    datasets = [DataLoader(data, batch_size=batch_size) for data in all_datasets]
    # print(datasets)
    # for data in social_datasets:
    #     combined += data
    #
    # # combined = transforms(combined)
    # print(combined)

    return datasets, names + ["ogbg-molesol", "facebook_large", "twitch_egos", "cora", "random", "community"]

    #
    # datasets = datasets + [get_fb_dataset(num = num_social),
    #                   get_deezer(num=2 * num_social),
    #                   get_cora_dataset(batch_size, num=num_social)]#,
    #                   # get_community_dataset(batch_size, num = num_social),
    #                   # get_random_dataset(batch_size, num = num_social)
    #                   # ]

    # large_dataset =

    # return [DataLoader(data, batch_size=batch_size) for data in datasets + [dataset]] +  social_loaders,\
    #        names +  ["Molesol (target)", "Facebook", "Egos", "Cora"]#, "Communities", "Random"]

    # out = torch.utils.data.ConcatDataset([datasets])
    #
    # return out

def get_evaluators(dataset, evaluator):

    task_type = ""
    device = "cuda" if torch.cuda.is_available() else "cpu"

    for batch in dataset:
        batch.to(device)
        y = batch.y
        # print(y)

        if y is None:
            task_type = "nodes"
            print("Nodes")
            break

        else:
            y = y[0]

        if type(y) == int:
            task_type = "graph-classification"
            print("graph-classification")
        elif type(y) == float:
            task_type = "graph-regression"
            print("graph-regression")
        else:
            task_type = "nodes"
            print("Nodes")
        break
    try:
        # if 'classification' in dataset.task_type:
        if task_type == "graph-classification":
            ee = EmbeddingEvaluation(LogisticRegression(dual=False, fit_intercept=True, max_iter=10000),
                                     evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
                                     param_search=True)
            # ee = EmbeddingEvaluation(MLPClassifier(max_iter=2000),
            #                          evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
            #                          param_search=True)
        elif task_type == "graph-regression":
            ee = EmbeddingEvaluation(Ridge(fit_intercept=True, copy_X=True, max_iter=10000),
                                     evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
                                     param_search=True)
            # ee = EmbeddingEvaluation(MLPRegressor(max_iter=2000),
            #                          evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
            #                          param_search=True)
        else:
            ee = DummyEmbeddingEvaluation(Ridge(fit_intercept=True, copy_X=True, max_iter=10000),
                                     evaluator, "", 0., device, params_dict=None,
                                     param_search=True)
    except:
        ee = DummyEmbeddingEvaluation(Ridge(fit_intercept=True, copy_X=True, max_iter=10000),
                                      evaluator, "", 0., device, params_dict=None,
                                      param_search=True)
    # else:
    #     raise NotImplementedError

    return ee


def vis_vals(loader, dataset_name, num = 10000):
    device = "cuda" if torch.cuda.is_available() else "cpu"

    data_directory = os.getcwd() + '/original_datasets/' + dataset_name
    if "vals" not in os.listdir(data_directory):
        print(f"Making {os.getcwd() + '/original_datasets/' + dataset_name + '/vals'}")
        os.mkdir(os.getcwd() + '/original_datasets/' + dataset_name + '/vals')

    existing_files = os.listdir(os.getcwd() + '/original_datasets/' + dataset_name + f'/vals')
    skip_all = True
    for i in range(num):
        if f"val-{i}.png" in existing_files:
            print(f"Files already exist for {dataset_name}")
            pass
        else:
            skip_all = False
            break

    if skip_all:
        return

    val_data = []
    for batch in loader:
        val_data += batch.to_data_list()

    existing_files = os.listdir(os.getcwd() + '/original_datasets/' + dataset_name + f'/vals')
    for i, data in enumerate(tqdm(val_data)):
        filename = os.getcwd() + '/original_datasets/' + dataset_name + f'/vals/val-{i}.png'
        if i >= num or f"val-{i}.png" in existing_files:
            # print(f"{filename} already exists")
            pass
        else:
            vis_from_pyg(data, filename=filename)


        # x_aug, _ = model(batch.batch, batch.x, batch.edge_index, batch.edge_attr, batch_aug_edge_weight)


def vis_views(view_learner, loader, dataset_name, num = 10000):
    device = "cuda" if torch.cuda.is_available() else "cpu"




    data_directory = os.getcwd() + '/original_datasets/' + dataset_name

    if "views" not in os.listdir(data_directory):
        print(f"Making {os.getcwd() + '/original_datasets/' + dataset_name + '/views'}")
        os.mkdir(os.getcwd() + '/original_datasets/' + dataset_name + '/views')


    existing_files = os.listdir(os.getcwd() + '/original_datasets/' + dataset_name + f'/views')
    skip_all = True
    for i in range(num):
        if f"view-{i}.png" in existing_files:
            pass
        else:
            print(f"Files already exist for {dataset_name}")
            skip_all = False
            break

    if skip_all:
        return


    view_data = []

    view_learner.eval()
    with torch.no_grad():
        for batch in loader:
            batch = batch.to(device)
            # print(ibatch, ibatch.ptr, ibatch.ptr.shape, ibatch.num_graphs, ibatch.edge_index)
            # if len(view_data) > num:
            #     break
            edge_logits = view_learner(batch.batch, batch.x, batch.edge_index, batch.edge_attr)

            temperature = 1.0
            bias = 0.0 + 0.0001  # If bias is 0, we run into problems
            eps = (bias - (1 - bias)) * torch.rand(edge_logits.size()) + (1 - bias)
            gate_inputs = torch.log(eps) - torch.log(1 - eps)
            gate_inputs = gate_inputs.to(device)
            gate_inputs = (gate_inputs + edge_logits) / temperature
            batch_aug_edge_weight = torch.sigmoid(gate_inputs).squeeze()

            kept = torch.bernoulli(batch_aug_edge_weight).to(torch.bool)
            print(f"Dropped {kept.shape[0] - torch.sum(kept)} edges out of {kept.shape[0]}")

            # Kept is a boolean array of whether an edge is kept after the view
            datalist = batch.to_data_list()
            edges_so_far = 0
            for idata, data in enumerate(datalist):

                dropped_slice = kept[edges_so_far:edges_so_far + data.num_edges]
                new_edges = data.edge_index[:,dropped_slice]
                edges_so_far += data.num_edges
                data.edge_index = new_edges
                datalist[idata] = data

            view_data += datalist


    existing_files = os.listdir(os.getcwd() + '/original_datasets/' + dataset_name + f'/views')
    for i, data in enumerate(tqdm(view_data)):
        filename = os.getcwd() + '/original_datasets/' + dataset_name + f'/views/view-{i}.png'
        if i >= num or f"view-{i}.png" in existing_files:
            # print(f"{filename} already exists")
            pass
        else:
            vis_from_pyg(data, filename=filename)


        # x_aug, _ = model(batch.batch, batch.x, batch.edge_index, batch.edge_attr, batch_aug_edge_weight)



def run(args):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    logging.info("Using Device: %s" % device)
    logging.info("Seed: %d" % args.seed)
    logging.info(args)
    setup_seed(args.seed)

    num = args.num
    redo_views = args.redo_views

    print(f"Redo-views: {redo_views}")

    evaluator = Evaluator(name=args.dataset)
    my_transforms = Compose([initialize_edge_weight])
    dataset = PygGraphPropPredDataset(name=args.dataset, root='./original_datasets/', transform=my_transforms)

    split_idx = dataset.get_idx_split()

    # train_loader = DataLoader(dataset[split_idx["train"]], batch_size=512, shuffle=True)
    # valid_loader = DataLoader(dataset[split_idx["valid"]], batch_size=512, shuffle=False)
    # test_loader = DataLoader(dataset[split_idx["test"]], batch_size=512, shuffle=False)


    # dataloader, names = get_big_dataset(dataset[split_idx["train"]], args.batch_size, my_transforms)
    val_loaders, names = get_val_loaders(dataset[split_idx["train"]], args.batch_size, my_transforms)



    model_dict = torch.load("outputs/Checkpoint-210-5.pt", map_location=torch.device('cpu'))

    model = GInfoMinMax(MoleculeEncoder(emb_dim=args.emb_dim, num_gc_layers=args.num_gc_layers, drop_ratio=args.drop_ratio, pooling_type=args.pooling_type),
                    proj_hidden_dim=args.emb_dim).to(device)

    model.load_state_dict(model_dict['encoder_state_dict'])
    # model_optimizer = torch.optim.Adam(model.parameters(), lr=args.model_lr)


    view_learner = ViewLearner(MoleculeEncoder(emb_dim=args.emb_dim, num_gc_layers=args.num_gc_layers, drop_ratio=args.drop_ratio, pooling_type=args.pooling_type),
                               mlp_edge_model_dim=args.mlp_edge_model_dim).to(device)
    view_learner.load_state_dict(model_dict['view_state_dict'])
    # view_optimizer = torch.optim.Adam(view_learner.parameters(), lr=args.view_lr)

    if redo_views:
        for i, loader in enumerate(val_loaders):
            vis_vals(loader, names[i], num = num)
            vis_views(view_learner, loader, names[i], num=num)

    if 'classification' in dataset.task_type:
        ee = EmbeddingEvaluation(LogisticRegression(dual=False, fit_intercept=True, max_iter=10000),
                                 evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
                                 param_search=True)
        # ee = EmbeddingEvaluation(MLPClassifier(max_iter=2000),
        #                          evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
        #                          param_search=True)
    elif 'regression' in dataset.task_type:
        ee = EmbeddingEvaluation(Ridge(fit_intercept=True, copy_X=True, max_iter=10000),
                                 evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
                                 param_search=True)
        # ee = EmbeddingEvaluation(MLPRegressor(max_iter=2000),
        #                          evaluator, dataset.task_type, dataset.num_tasks, device, params_dict=None,
        #                          param_search=True)
    else:
        raise NotImplementedError

    general_ee = GeneralEmbeddingEvaluation()

    model.eval()
    all_embeddings, separate_embeddings = general_ee.get_embeddings(model.encoder, val_loaders)

    # embedder = UMAP(n_components=2, n_neighbors=240, n_jobs=4, verbose=1).fit(all_embeddings)
    embedder = PCA(n_components=2).fit(all_embeddings)
    # embedder = FastICA(n_components=2).fit(all_embeddings)
    # embedder = TruncatedSVD(n_components=2).fit(all_embeddings)

    x, y, plot_names, plot_paths, view_paths  = [], [], [], [], []
    ind_x, ind_y, ind_plot_names, ind_plot_paths, ind_view_paths = [], [], [], [], []

    for i, emb in enumerate(separate_embeddings):
        proj = embedder.transform(emb)

        proj_x, proj_y = proj[:num,0].tolist(), proj[:num,1].tolist()
        x += proj_x
        y += proj_y

        ind_x.append(proj_x)
        ind_y.append(proj_y)

        plot_names += len(proj_x)*[names[i]]

        # ind_plot_names.append(len(proj_x)*[names[i]])

        ind_plot_names.append(names[i])

        img_root = os.getcwd() + '/original_datasets/' + names[i] + '/vals/*.png'
        # print(img_root)
        img_paths = glob.glob(img_root)

        filenames = [int(filename.split('/')[-1][4:-4]) for filename in img_paths]
        sort_inds = np.argsort(filenames).tolist()
        # print(img_paths)
        plot_paths += [img_paths[ind] for ind in sort_inds][:num]

        ind_plot_paths.append([img_paths[ind] for ind in sort_inds][:num])


        img_root = os.getcwd() + '/original_datasets/' + names[i] + '/views/*.png'
        # print(img_root)
        img_paths = glob.glob(img_root)

        filenames = [int(filename.split('/')[-1][5:-4]) for filename in img_paths]
        sort_inds = np.argsort(filenames).tolist()
        view_paths += [img_paths[ind] for ind in sort_inds]

        ind_view_paths.append([img_paths[ind] for ind in sort_inds])



    # print(x,y,plot_names,plot_paths)

    print(len(x), len(y), len(plot_names), len(plot_paths), len(view_paths))
    output_file("toolbar.html")



    # scatter_source = ColumnDataSource(
    #     data=dict(
    #         x=x,
    #         y=y,
    #         desc=plot_names
    #     )
    # )

    # <div>
    #     <span style="font-size: 15px;">Location</span>
    #     <span style="font-size: 10px; color: #696;">($x, $y)</span>
    # </div>

    hover = HoverTool(
        tooltips="""
            <div>
                <div>
                    <span style="font-size: 15px; font-weight: bold;">@desc</span>
                    <span style="font-size: 10px;">Original and View</span>
                </div>
                <div>
                    <img
                        src="@imgs" height="128" alt="missing" width="128"
                        style="float: left; margin: 0px 0px 0px 0px;"
                        border="2"
                    ></img>
                    <img
                        src="@views" height="128" alt="missing" width="128"
                        style="float: left; margin: 0px 0px 0px 0px;"
                        border="2"
                    ></img>
                </div>

            </div>
            """
    )

    p = figure(tools=[hover,BoxZoomTool(), ResetTool()],
               title="Mouse over the dots", aspect_ratio = 16/9, lod_factor = 10)

    palette = d3['Category10'][len(set(plot_names))]
    color_map = bmo.CategoricalColorMapper(factors=tuple(set(plot_names)),
                                           palette=palette)

    unique_names = np.arange(len(names))

    colors = [
        "#%02x%02x%02x" % (int(r), int(g), int(b)) for r, g, b, _ in 255 * mpl.cm.tab20(mpl.colors.Normalize()(unique_names))
    ]

    print(len(names), len(ind_x), len(ind_y), len(ind_plot_paths), len(ind_view_paths))
    for i in range(len(names)):
        name = names[i]
    # for i, name in enumerate(names):
        print(name,
              len(ind_x[i]),
                len(ind_y[i]),
                # desc= ind_x[i].shape[0] * [name],
                len(ind_plot_paths[i]),
                len(ind_view_paths[i]))

        source = ColumnDataSource(
            data=dict(
                x=ind_x[i],
                y=ind_y[i],
                desc= len(ind_x[i]) * [name],
                imgs=ind_plot_paths[i],
                views=ind_view_paths[i]
            )
        )

        p.scatter("x", "y", color = colors[i], size=5, alpha=0.4, legend_label=name, source=source)
    p.legend.click_policy = "hide"
    p.legend.location = "top_left"
    p.sizing_mode = 'scale_width'


    x_array, y_array = np.array(sorted(x)), np.array(sorted(y))
    n_points = x_array.shape[0]
    outlier_x = [x_array[int(0.005*n_points)],x_array[int(0.995*n_points)]]
    outlier_y = [y_array[int(0.005*n_points)], y_array[int(0.995*n_points)]]

    upper_lim_x, lower_lim_x = max(outlier_x), min(outlier_x)
    upper_lim_y, lower_lim_y = max(outlier_y), min(outlier_y)

    p.x_range = Range1d(lower_lim_x, upper_lim_x)
    p.y_range = Range1d(lower_lim_y, upper_lim_y)

    p.xaxis.axis_label = f"{embedder} 0"
    p.yaxis.axis_label = f"{embedder} 1"

    show(p)




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
    parser.add_argument('--mlp_edge_model_dim', type=int, default=128,
                        help='embedding dimension')
    parser.add_argument('--batch_size', type=int, default=512,
                        help='batch size')
    parser.add_argument('--drop_ratio', type=float, default=0.2,
                        help='Dropout Ratio / Probability')
    parser.add_argument('--epochs', type=int, default=5000,
                        help='Train Epochs')
    parser.add_argument('--reg_lambda', type=float, default=5.0, help='View Learner Edge Perturb Regularization Strength')

    parser.add_argument('--seed', type=int, default=0)

    parser.add_argument('--num', type=int, default=5000,
                        help='Number of points included in each dataset')

    parser.add_argument('--redo_views', type=bool, default=False,
                        help='Whether to re-vis views')

    # parser.add_argument('--seed', type=int, default=0)

    return parser.parse_args()


if __name__ == '__main__':

    args = arg_parse()
    args = setup_wandb(args)
    # print(args.dataset, repr(args))
    # quit()
    run(args)
