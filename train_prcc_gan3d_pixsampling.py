# encoding: utf-8

import argparse
import os
import sys
import torch
from torch.backends import cudnn

sys.path.append('.')
from data import make_data_loader_prcc_gan3dnet as make_data_loader
from engine.trainer import do_train_prcc_gan3d_pix_mse as do_train
from modeling import build_model
from layers import make_loss_with_triplet_entropy_mse,make_loss_with_triplet_entropy
from solver import make_optimizer_with_triplet
from solver import WarmupMultiStepLR
from engine.inference import inference_prcc_ganmesh,inference_prcc_ganmeshper4, inference_prcc_visual_rank
import datetime


def load_network_pretrain(model, cfg):
    path = os.path.join(cfg.logs_dir, 'checkpoint_best.pth')
    if not os.path.exists(path):
        return model, 0, 0.0
    pre_dict = torch.load(path)
    model.load_state_dict(pre_dict['state_dict'])
    start_epoch = pre_dict['epoch']
    best_acc = pre_dict['best_acc']
    print('start_epoch:', start_epoch)
    print('best_acc:', best_acc)
    return model, start_epoch, best_acc



def main(cfg):
    # prepare dataset
    train_loader, val_loader_c, num_query_c, num_classes = make_data_loader(cfg, h=256, w=128)  # num_query=3368, num_classes=751
    # prepare model
    model = build_model(num_classes, 'mesh3d',cfg.group)
    model = torch.nn.DataParallel(model).cuda() if torch.cuda.is_available() else model
    mesh_model = build_model(num_classes, 'mesh3d',cfg.group)
    mesh_model = torch.nn.DataParallel(mesh_model).cuda() if torch.cuda.is_available() else mesh_model

    loss_func = make_loss_with_triplet_entropy_mse(cfg, num_classes)
    optimizer = make_optimizer_with_triplet(cfg, model)
    mesh_loss_func = make_loss_with_triplet_entropy(cfg, num_classes)
    mesh_optimizer = make_optimizer_with_triplet(cfg, mesh_model)

    if cfg.lr_type == 'step':
        scheduler = WarmupMultiStepLR(optimizer, cfg.steps, cfg.gamma, cfg.warmup_factor, cfg.warmup_iters, cfg.warmup_method)
        mesh_scheduler = WarmupMultiStepLR(mesh_optimizer, cfg.steps, cfg.gamma, cfg.warmup_factor, cfg.warmup_iters,
                                      cfg.warmup_method)
    else:
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'max', patience=10, factor=0.5)
        mesh_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(mesh_optimizer, 'max', patience=10, factor=0.5)

    if cfg.train == 'train':
        start_epoch = int(0)
        acc_best = 0.0
        if cfg.resume == 1:
            model, start_epoch, acc_best = load_network_pretrain(model, cfg)

        do_train(cfg, model,mesh_model, train_loader, val_loader_c, optimizer,mesh_optimizer, scheduler,mesh_scheduler, loss_func,mesh_loss_func, num_query_c, start_epoch, acc_best, lr_type='step')
    elif cfg.train == 'test':
        # Test
        last_model_wts = torch.load(os.path.join(cfg.logs_dir, 'gancheckpoint_best.pth'))
        model.load_state_dict(last_model_wts['state_dict'])

        last_model_wts = torch.load(os.path.join(cfg.logs_dir, 'meshcheckpoint_best.pth'))
        mesh_model.load_state_dict(last_model_wts['state_dict'])

        if cfg.group:
            mAP, cmc1 = inference_prcc_ganmeshper4(model,mesh_model, val_loader_c, num_query_c)
        else:
            mAP, cmc1 = inference_prcc_ganmesh(model, mesh_model, val_loader_c, num_query_c)
        start_time = datetime.datetime.now()
        start_time = '%4d:%d:%d-%2d:%2d:%2d' % (start_time.year, start_time.month, start_time.day, start_time.hour, start_time.minute, start_time.second)
        line = '{} - Test: cmc1: {:.1%}, mAP: {:.1%}\n'.format(start_time, cmc1, mAP)
        print(line)
    elif cfg.train == 'rank':
        # Test
        last_model_wts = torch.load(os.path.join(cfg.logs_dir, 'checkpoint_best.pth'))
        model.load_state_dict(last_model_wts['state_dict'])

        home = os.path.join('logs', 'rank', os.path.basename(cfg.logs_dir))
        inference_prcc_visual_rank(model, val_loader_c, num_query_c, home=home, show_rank=20, use_flip=True)
        print('finish')





if __name__ == '__main__':
    gpu_id = 0
    os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
    cudnn.benchmark = True
    parser = argparse.ArgumentParser(description="ReID Baseline Training")

    # DATA
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--img_per_id', type=int, default=4)
    parser.add_argument('--batch_size_test', type=int, default=128)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--height', type=int, default=256)
    parser.add_argument('--width', type=int, default=128)
    parser.add_argument('--height_mask', type=int, default=256)
    parser.add_argument('--width_mask', type=int, default=128)
    parser.add_argument('--group', action='store_true',default=True, help='Whether to enable group!')


    # MODEL
    parser.add_argument('--features', type=int, default=128)
    parser.add_argument('--dropout', type=float, default=0.0)
    parser.add_argument('--parts', type=int, default=14)

    # OPTIMIZER
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--lr', type=float, default=0.00035)
    parser.add_argument('--lr_center', type=float, default=0.5)
    parser.add_argument('--lr_type', type=str, default='step', help='step, plateau')
    parser.add_argument('--center_loss_weight', type=float, default=0.0005)
    parser.add_argument('--steps', type=list, default=[40, 80])
    parser.add_argument('--gamma', type=float, default=0.1)
    parser.add_argument('--cluster_margin', type=float, default=0.3)
    parser.add_argument('--bias_lr_factor', type=float, default=1.0)
    parser.add_argument('--weight_decay', type=float, default=5e-4)
    parser.add_argument('--weight_decay_bias', type=float, default=5e-4)
    parser.add_argument('--range_k', type=float, default=2)
    parser.add_argument('--range_margin', type=float, default=0.3)
    parser.add_argument('--range_alpha', type=float, default=0)
    parser.add_argument('--range_beta', type=float, default=1)
    parser.add_argument('--range_loss_weight', type=float, default=1)
    parser.add_argument('--warmup_factor', type=float, default=0.01)
    parser.add_argument('--warmup_iters', type=float, default=10)
    parser.add_argument('--warmup_method', type=str, default='linear')
    parser.add_argument('--margin', type=float, default=0.3)
    parser.add_argument('--optimizer_name', type=str, default="SGD", help="SGD, Adam")
    parser.add_argument('--momentum', type=float, default=0.9)


    # TRAINER
    parser.add_argument('--max_epochs', type=int, default=60)
    parser.add_argument('--train', type=str, default='train', help='train, test, rank')  # change train or test mode
    parser.add_argument('--resume', type=int, default=0)
    parser.add_argument('--num_works', type=int, default=0)

    # misc
    working_dir = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument('--dataset', type=str, default='gan3d_msk')
    parser.add_argument('--data_dir', type=str, default='/disk/yxh/PRCC/')
    parser.add_argument('--logs_dir', type=str, default=os.path.join(working_dir, 'logs/prcc_gan3dper4_pix_mse'))

    cfg = parser.parse_args()
    if not os.path.exists(cfg.logs_dir):
        os.makedirs(cfg.logs_dir)

    main(cfg)
