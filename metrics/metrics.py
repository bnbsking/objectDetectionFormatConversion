import copy
import json
import glob
import os
from tqdm import tqdm
from typing import Dict, List, Tuple, Union

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import cv2
import confusion_matrix
#import visualization as viz


class MetricsPipeline:
    def __init__(
            self,
            num_classes: int,
            labels: List[np.ndarray],
            detections: List[np.ndarray],
            func_dicts: List[Dict],
        ):
        """
        Given basic arguments, self.run iterates func_dicts and returns all metrics as results.
        Args:
            num_classes (int): number of classes
            labels (List[np.ndarray]): length is the number of images. each numpy has shape (N, 5).
                N is the number of ground truth, and 5 refers to (cid, xmin, ymin, xmax, ymax)
            detections (List[np.ndarray]):length is the number of images. each numpy has shape (M, 5).
                M is the number of predictions, and 6 refers to (xmin, ymin, xmax, ymax, conf, cid)
            func_dicts (List[Dict]): length is the number of metrics function.
                each dict has the format {"func_name": str, "args": Dict, "log_name": str}.
                self.run saves the output in self.metrics, where log_name is key and output is value
        """
        self.num_classes = num_classes
        self.labels = labels
        self.detections = detections
        self.func_dicts = func_dicts
        self.metrics = {}

    def run(self):
        for func_dict in self.func_dicts:
            self.metrics[func_dict["log_name"]] = \
                getattr(self, func_dict["func_name"])(**func_dict["func_args"])
        return self.metrics

    def get_pr_curves(self, k: int = 101) -> List[Dict[str, List[float]]]:
        pr_curves = [
            {
                "precision": [0.] * k,
                "recall": [0.] * k,
            } for _ in range(self.num_classes)
        ]

        for i, threshold in tqdm(enumerate(np.linspace(0, 1, k))):
            # get confusion of the threshold
            confusion = np.zeros(
                (self.num_classes + 1, self.num_classes + 1)
            )  # (i, j) = (pd, gt)
            for label, detection in zip(self.labels, self.detections):
                img_confusion = confusion_matrix.ConfusionMatrix(
                    self.num_classes,
                    CONF_THRESHOLD = threshold,
                    IOU_THRESHOLD = 0.5
                )
                img_confusion.process_batch(detection, label)
                confusion += img_confusion.return_matrix()
            
            # update pr curve at the threshold from confusion
            row_sum = confusion.sum(axis=1)
            col_sum = confusion.sum(axis=0)
            for cid in range(self.num_classes):
                pr_curves[cid]["precision"][i] = confusion[cid][cid]/row_sum[cid] if row_sum[cid] else 0
                pr_curves[cid]["recall"][i] = confusion[cid][cid]/col_sum[cid] if col_sum[cid] else 0

        return pr_curves

    def get_refine_pr_curves(self, pr_curves_key = "pr_curves") -> List[Dict[str, List[float]]]:
        """
        sorted by recall, and enhance precision by next element reversely
        Args:
            pr_curves_key (str): get pr_curves from self.metrics and refine it.
                you must call self.get_pr_curves in advance
        """
        pr_curves = copy.deepcopy(self.metrics[pr_curves_key])
        for cid in range(len(pr_curves)):
            recall_arr = pr_curves[cid]["recall"].copy()
            precision_arr = pr_curves[cid]["precision"].copy()
            zip_arr = sorted(zip(recall_arr, precision_arr))
            recall_arr, precision_arr = zip(*zip_arr)
            recall_arr, precision_arr = list(recall_arr), list(precision_arr)
            for i in range(1, len(precision_arr)):
                precision_arr[-1-i] = max(precision_arr[-1-i], precision_arr[-i])
            pr_curves[cid]["refine_recall"] = recall_arr
            pr_curves[cid]["refine_precision"] = precision_arr
        return pr_curves
    
    def get_aps(self, refine_pr_curves_key: str, gt_class_cnts: List[int] = None) -> Dict:
        """
        Args:
            refine_pr_curves_key (str): get refine_pr_curves from self.metrics and compute aps
                you must call self.get_refine_pr_curves in advance
        """
        pr_curves = self.metrics[refine_pr_curves_key]
        aps = {
            "ap_list": [],
            "map": -1,
            "wmap": -1
        }
        num_classes = len(pr_curves)
        for cid in range(num_classes):
            ap = 0
            for i in range(len(pr_curves[0]["precision"])-1):  # 101-1
                ap += pr_curves[cid]["refine_precision"][i] * \
                    (pr_curves[cid]["refine_recall"][i+1] - pr_curves[cid]["refine_recall"][i])
            aps["ap_list"].append(round(ap,3))
        aps["map"] = round(sum(aps["ap_list"]) / num_classes, 3)
        aps["wmap"] = round(
            sum(ap * cnt for ap, cnt in zip(aps["ap_list"], gt_class_cnts)) / sum(gt_class_cnts), 3)\
            if gt_class_cnts else -1
        return aps
    
    def get_best_threshold(self, strategy: str = "f1", **kwargs):
        if strategy in {"f1", "precision"}:
            if strategy == "f1":
                score_func = lambda precision, recall: 2 * precision * recall / (precision + recall + 1e-10)
            elif strategy == "precision":
                score_func = lambda precision, recall: precision if recall >= 0.5 else 0
            pr_curves_key = kwargs["pr_curves_key"]
            gt_class_cnts = kwargs["gt_class_cnts"]

            pr_curves = self.metrics[pr_curves_key]
            num_classes = len(pr_curves)
            thresholds = np.linspace(0, 1, len(pr_curves[0]["precision"]))  # 101
            weighted_score = [0] * len(thresholds)
            for cid in range(num_classes):
                for i, (precision, recall) in enumerate(zip(pr_curves[cid]["precision"], pr_curves[cid]["recall"])):
                    score = score_func(precision, recall)
                    weighted_score[i] += score * gt_class_cnts[cid] / sum(gt_class_cnts)
            best_score, best_threshold = max(zip(weighted_score, thresholds))
            return {"best_score": best_score, "best_threshold": best_threshold}

    def plotConfusion(self):
        self.strategy = strategy
        n = len(self.classL)
        M = np.zeros( (n+1,n+1) ) # col:gt, row:pd
        self.accumFileL = [ [[] for j in range(n+1)] for i in range(n+1) ] # (n+1,n+1) each grid is path list
        for j,(imgPath,labels,detections) in enumerate(zip(self.imgPathL,self.labels,self.detections)):
            cm = confusion_matrix.ConfusionMatrix(n, CONF_THRESHOLD=self.bestThreshold, IOU_THRESHOLD=0.5, gtFile=imgPath, accumFileL=self.accumFileL)
            cm.process_batch(detections,labels)
            M += cm.return_matrix()
            self.accumFileL = cm.getAccumFileL()


class PlottingPipeline:
    def __init__(self, class_list: List[str], save_folder: str, func_dicts: List[Dict]):
        self.class_list = class_list
        self.save_folder = save_folder
        self.func_dicts = func_dicts
        os.makedirs(save_folder, exist_ok=True)

    def run(self):
        for func_dict in self.func_dicts:
            getattr(self, func_dict["func_name"])(**func_dict["func_args"])
    
    def plot_aps(self, ap_list: List[float], map: float, wmap: float = -1):
        plt.figure()
        ax = plt.subplot(1, 1, 1)
        ax.set_title(f"map={round(map, 3)}, wmap={round(wmap, 3)}", fontsize=16)
        ax.bar(self.class_list, ap_list)
        for i in range(len(self.class_list)):
            ax.text(i, ap_list[i], ap_list[i], ha="center", va="bottom", fontsize=16)
        plt.savefig(os.path.join(self.save_folder, "aps.jpg"))
        plt.show()

    def plot_pr_curves(self, refine_pr_curves: List[Dict[str, List[float]]]):
        num_classes = len(refine_pr_curves)
        plt.figure(figsize=(6 * num_classes, 4))
        for cid in range(num_classes):
            plt.subplot(1, num_classes, 1 + cid)
            plt.scatter(refine_pr_curves[cid]["refine_recall"], refine_pr_curves[cid]["refine_precision"])
            plt.plot(refine_pr_curves[cid]["refine_recall"], refine_pr_curves[cid]["refine_precision"])
            plt.xlim(-0.05, 1.05)
            plt.ylim(-0.05, 1.05)
            plt.grid('on')
            plt.title(f"{cid}-{self.class_list[cid]}", fontsize=16)
            plt.xlabel("recall", fontsize=16)
            plt.ylabel("precision", fontsize=16)
        plt.savefig(os.path.join(self.save_folder, "pr_curves.jpg"))
        plt.show()

    def plot_prf_curves(self, pr_curves: List[Dict[str, List[float]]]):
        num_classes = len(pr_curves)
        plt.figure(figsize=(6 * num_classes, 4))
        for cid in range(num_classes):
            f1_arr = [2 * p * r / (p + r + 1e-10) for p, r in \
                zip(pr_curves[cid]["precision"], pr_curves[cid]["recall"])]
            plt.subplot(1, num_classes, 1 + cid)
            plt.plot(pr_curves[cid]["precision"])
            plt.plot(pr_curves[cid]["recall"])
            plt.plot(f1_arr)
            plt.xlim(-5, 105)
            plt.ylim(-0.05, 1.05)
            plt.grid('on')
            plt.title(f"{cid}-{self.class_list[cid]}", fontsize=16)
            plt.xlabel("threshold", fontsize=16)
            plt.legend(labels=["precision", "recall", "f1"], fontsize=12)
        plt.savefig(os.path.join(self.save_folder, "prf_curves.jpg"))
        plt.show()


class Result:
    """
    valTxtPath = "/home/jovyan/data-vol-1/yolov7/_data/sample200new/val.txt"
    pdJsonPath = "/home/jovyan/data-vol-1/yolov7/runs/test/sample200_lr1_raw/best_predictions.json"
    obj = Result(valTxtPath, pdJsonPath, classL=['tetra'], classNumL=[1])
    obj.getPR()
    obj.getRefineRP()
    obj.getAPs()
    obj.plotPR()
    obj.plotConfusion()
    obj.getBlockImgs(1,0)
    """
    # def __init__(self, imgPathL, antPathL, labels, detections, classL, classNumL, savePath):
    #     self.imgPathL = imgPathL
    #     self.antPathL = antPathL
    #     self.labels   = labels
    #     self.detections = detections
    #     self.classL    = classL
    #     self.classNumL = classNumL
    #     self.savePath  = savePath
    
    def __init__(self, ant_path: str, save_folder: str):
        with open(ant_path, "r", encoding="utf-8") as f:
            general = json.load(f)
        
        class_list = general["categories"]
        num_classes = len(general["categories"])
        labels = self.get_labels(general["data"])
        detections = self.get_detections(general["data"])
        gt_class_cnts = self.get_gt_class_cnts(num_classes, general["data"])

        pipeline = MetricsPipeline(
            num_classes = num_classes,
            labels = labels,
            detections = detections,
            func_dicts = [
                {
                    "func_name": "get_pr_curves",
                    "func_args": {},
                    "log_name": "pr_curves"
                },
                {
                    "func_name": "get_refine_pr_curves",
                    "func_args": {"pr_curves_key": "pr_curves"},
                    "log_name": "refine_pr_curves"
                },
                {
                    "func_name": "get_aps",
                    "func_args": {
                        "refine_pr_curves_key": "refine_pr_curves",
                        "gt_class_cnts": gt_class_cnts
                    },
                    "log_name": "aps"
                },
                {
                    "func_name": "get_best_threshold",
                    "func_args": {
                        "strategy": "f1",
                        "pr_curves_key": "pr_curves",
                        "gt_class_cnts": gt_class_cnts
                    },
                    "log_name": "best_threshold"
                },
                # {
                #     "func_name": "get_confusion",
                #     "func_args": {
                #         "threshold": "pr_curves",
                #         "gt_class_cnts": gt_class_cnts
                #     },
                #     "log_name": "best_threshold"
                # }
            ]
        )
        metrics = pipeline.run()
        print(metrics)

        plotting = PlottingPipeline(
            class_list = class_list,
            save_folder = save_folder,
            func_dicts = [
                {
                    "func_name": "plot_aps",
                    "func_args": metrics["aps"],
                },
                {
                    "func_name": "plot_pr_curves",
                    "func_args": {"refine_pr_curves": metrics["refine_pr_curves"]},
                },
                {
                    "func_name": "plot_prf_curves",
                    "func_args": {"pr_curves": metrics["pr_curves"]},
                }
            ]
        )
        plotting.run()

    def get_labels(self, data_dict_list: List[Dict]) -> List[np.ndarray]:
        labels = []
        for data_dict in data_dict_list:
            img_label = []
            for cid, (xmin, ymin, xmax, ymax) in zip(data_dict["gt_cls"], data_dict["gt_boxes"]):
                img_label.append([cid, xmin, ymin, xmax, ymax])
            labels.append(np.array(img_label))
        return labels

    def get_detections(self, data_dict_list: List[Dict]) -> List[np.ndarray]:
        detections = []
        for data_dict in data_dict_list:
            img_detect = []
            for probs, (xmin, ymin, xmax, ymax) in zip(data_dict["pd_probs"], data_dict["pd_boxes"]):
                conf = max(probs)
                cid = probs.index(conf)
                img_detect.append([xmin, ymin, xmax, ymax, conf, cid])
            detections.append(np.array(img_detect))
        return detections

    def get_gt_class_cnts(self, num_classes: int, data_dict_list: List[Dict]) -> List[int]:
        gt_class_cnts = [0] * num_classes
        for data_dict in data_dict_list:
            for gt_cls in data_dict["gt_cls"]:
                gt_class_cnts[gt_cls] += 1
        return gt_class_cnts
    
    def plotConfusion(self, strategy="fvalue"):
        self.strategy = strategy
        n = len(self.classL)
        M = np.zeros( (n+1,n+1) ) # col:gt, row:pd
        self.accumFileL = [ [[] for j in range(n+1)] for i in range(n+1) ] # (n+1,n+1) each grid is path list
        for j,(imgPath,labels,detections) in enumerate(zip(self.imgPathL,self.labels,self.detections)):
            cm = confusion_matrix.ConfusionMatrix(n, CONF_THRESHOLD=self.bestThreshold, IOU_THRESHOLD=0.5, gtFile=imgPath, accumFileL=self.accumFileL)
            cm.process_batch(detections,labels)
            M += cm.return_matrix()
            self.accumFileL = cm.getAccumFileL()
        #
        #print(M)
        axis0sum = M.sum(axis=0)
        N = M.copy()
        for i in range(len(N)):
            if axis0sum[i] != 0:
                N[:,i] /= axis0sum[i]
        #print(N)
        axis1sum = M.sum(axis=1)
        P = M.copy()
        for i in range(len(P)):
            if axis1sum[i] != 0:
                P[i,:] /= axis1sum[i]
        #print(P)
        #
        plt.figure(figsize=(15,5))
        # fig1 - number
        fig = plt.subplot(1,3,1)
        plt.title(f"Confusion Matrix - Number (conf={self.bestThreshold})", fontsize=12)
        plt.xlabel("GT", fontsize=12)
        plt.ylabel("PD", fontsize=12)
        fig.set_xticks(np.arange(n+1)) # values
        fig.set_xticklabels(self.classL+['BG']) # labels
        fig.set_yticks(np.arange(n+1)) # values
        fig.set_yticklabels(self.classL+['BG']) # labels
        plt.imshow(P, cmap=mpl.cm.Blues, interpolation='nearest', vmin=0, vmax=1)
        for i in range(n+1):
            for j in range(n+1):
                plt.text(j, i, int(M[i][j]), ha="center", va="center", color="black" if P[i][j]<0.9 else "white", fontsize=12)
        # fig2 - precision
        fig = plt.subplot(1,3,2)
        plt.title(f"Confusion Matrix - Row norm (Precision)", fontsize=12)
        plt.xlabel("GT", fontsize=12)
        plt.ylabel("PD", fontsize=12)
        fig.set_xticks(np.arange(n+1)) # values
        fig.set_xticklabels(self.classL+['BG']) # labels
        fig.set_yticks(np.arange(n+1)) # values
        fig.set_yticklabels(self.classL+['BG']) # labels
        plt.imshow(P, cmap=mpl.cm.Blues, interpolation='nearest', vmin=0, vmax=1)
        for i in range(n+1):
            for j in range(n+1):
                plt.text(j, i, round(P[i][j],2), ha="center", va="center", color="black" if P[i][j]<0.9 else "white", fontsize=12)
        # fig3 - recall
        fig = plt.subplot(1, 3, 3)
        plt.title(f"Confusion Matrix - Col norm (Recall)", fontsize=12)
        plt.xlabel("GT", fontsize=12)
        plt.ylabel("PD", fontsize=12)
        fig.set_xticks(np.arange(n+1)) # values
        fig.set_xticklabels(self.classL+['BG']) # labels
        fig.set_yticks(np.arange(n+1)) # values
        fig.set_yticklabels(self.classL+['BG']) # labels
        plt.imshow(N, cmap=mpl.cm.Blues, interpolation='nearest', vmin=0, vmax=1)
        for i in range(n+1):
            for j in range(n+1):
                plt.text(j, i, round(N[i][j],2), ha="center", va="center", color="black" if N[i][j]<0.9 else "white", fontsize=12)
        #plt.colorbar(mpl.cm.ScalarMappable(cmap=mpl.cm.Blues))
        plt.savefig(f"{self.savePath}/confusion.jpg")
        plt.show()
        json.dump(self.accumFileL, open(f"{self.savePath}/confusionFiles.json","w"))
        
    def getBlockImgs(self, row, col, threshold='best'): # PD, GT
        classL = self.classL + ['BG']
        folder = f"{self.savePath}/GT_{classL[col]}_PD_{classL[row]}"
        os.makedirs(folder, exist_ok=True)
        threshold = self.bestThreshold if threshold=='best' else threshold 
        for imgPath in self.accumFileL[row][col]:
            idx = self.imgPathL.index(imgPath)
            det = self.detections[idx]
            det = det[det[:,4]>threshold]
            viz.show(self.classL, imgPath, imgPath.replace('.jpg','.xml'), 'voc', det[:,:4], det[:,5].astype(int), det[:,4], folder )
        print(f"len(glob.glob(folder+'/*.jpg'))={len(glob.glob(folder+'/*.jpg'))}")

obj = Result(
    ant_path = "../example/data/gt_and_pd.json",
    save_folder = "."
)
