import json
import os
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from tqdm import tqdm
import torch_geometric as pyg
from torch_geometric.data import InMemoryDataset
from torch_geometric.utils.convert import to_networkx
from torch_geometric.io import read_npz
import imageio
import wandb
# import osmnx as ox
from littleballoffur.exploration_sampling import MetropolisHastingsRandomWalkSampler, DiffusionSampler, ForestFireSampler
from sklearn.preprocessing import OneHotEncoder
# from ToyDatasets import *
import pickle
import zipfile
import wget
from networkx import community as comm


def download_cora(visualise = False):
    zip_url = "https://github.com/abojchevski/graph2gauss/raw/master/data/cora_ml.npz"

    start_dir = os.getcwd()
    # print(os.getcwd(), os.listdir())
    os.chdir("original_datasets")

    if "cora" not in os.listdir():
        print("Downloading CORA")
        os.mkdir("cora")
        os.chdir("cora")
        _ = wget.download(zip_url)
    else:
        os.chdir("cora")

    edges = read_npz("cora_ml.npz")
    G = to_networkx(edges, to_undirected=True)

    node_classes = {n: edges.y[i].item() for i, n in enumerate(list(G.nodes()))}

    base_tensor = torch.Tensor([0, 0, 0, 0, 0, 0, 0, 0, 0])

    for node in list(G.nodes()):
        class_tensor = base_tensor
        class_tensor[0] = node_classes[node]

        G.nodes[node]["attrs"] = class_tensor

    for edge in list(G.edges()):
        G.edges[edge]["attrs"] = torch.Tensor([1,0,0])

    CGs = [G.subgraph(c) for c in nx.connected_components(G)]
    CGs = sorted(CGs, key=lambda x: x.number_of_nodes(), reverse=True)
    graph = CGs[0]
    graph = nx.convert_node_labels_to_integers(graph)

    os.chdir(start_dir)
    return graph

def ESWR(graph, n_graphs, size):
    # print(f"Sampling {n_graphs} of size {size} from a {graph}")
    sampler = MetropolisHastingsRandomWalkSampler(number_of_nodes=size)
    graphs = [nx.convert_node_labels_to_integers(sampler.sample(graph)) for _ in tqdm(range(n_graphs))]

    return graphs

def get_cora_dataset(num = 2000):
    fb_graph = download_cora()
    # print(fb_graph.nodes(data=True))
    nx_graph_list = ESWR(fb_graph, num, 48)

    # loader = pyg.loader.DataLoader([pyg.utils.from_networkx(g, group_node_attrs=all, group_edge_attrs=all) for g in nx_graph_list],
    #                                           batch_size=batch_size)

    data_objects = [pyg.utils.from_networkx(g, group_node_attrs=all, group_edge_attrs=all) for g in nx_graph_list]
    for data in data_objects:
        data.y = None #torch.Tensor([[0,0]])

    return  data_objects# loader

class CoraDataset(InMemoryDataset):
    def __init__(self, root, stage = "train", transform=None, pre_transform=None, pre_filter=None, num = 2000):
        self.num = num
        self.stage = stage
        self.stage_to_index = {"train":0,
                               "val":1,
                               "test":2}
        _ = download_cora()
        super().__init__(root, transform, pre_transform, pre_filter)
        self.data, self.slices = torch.load(self.processed_paths[self.stage_to_index[self.stage]])


    @property
    def raw_file_names(self):
        return ['cora_ml.npz']

    @property
    def processed_file_names(self):
        return ['train.pt',
                'val.pt',
                'test.pt']


    def process(self):
        # Read data into huge `Data` list.
        data_list = get_cora_dataset(num=self.num)

        if self.pre_filter is not None:
            data_list = [data for data in data_list if self.pre_filter(data)]

        if self.pre_transform is not None:
            data_list = [self.pre_transform(data) for data in data_list]

        data, slices = self.collate(data_list)
        torch.save((data, slices), self.processed_paths[self.stage_to_index[self.stage]])


if __name__ == "__main__":
    # fb_graph = download_cora()
    # print(fb_graph.nodes(data=True))
    # graphs = ESWR(fb_graph, 200, 100)
    # G = download_cora()
    # print(G)
    dataset = CoraDataset(os.getcwd()+'/original_datasets/'+'cora')