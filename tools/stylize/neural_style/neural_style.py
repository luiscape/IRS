import argparse
import os
import sys
import time
import re

import numpy as np
import torch
from torch.optim import Adam
from torch.utils.data import DataLoader
from torchvision import datasets
from torchvision import transforms
import torch.onnx

import utils
from transformer_net import TransformerNet
from vgg import Vgg16


def check_paths(args):
    try:
        if not os.path.exists(args.save_model_dir):
            os.makedirs(args.save_model_dir)
        if args.checkpoint_model_dir is not None and not (os.path.exists(args.checkpoint_model_dir)):
            os.makedirs(args.checkpoint_model_dir)
    except OSError as e:
        print(e)
        sys.exit(1)


def train(args):
    device = torch.device("cuda" if args.cuda else "cpu")

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    transform = transforms.Compose([
        transforms.Resize(args.image_size),
        transforms.CenterCrop(args.image_size),
        transforms.ToTensor(),
        transforms.Lambda(lambda x: x.mul(255))
    ])
    train_dataset = datasets.ImageFolder(args.dataset, transform)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size)

    #style_dataset = datasets.ImageFolder(args.style_dataset, transform)
    #style_loader = DataLoader(style_dataset, batch_size=args.batch_size)

    transformer = TransformerNet().to(device)
    optimizer = Adam(transformer.parameters(), args.lr)
    mse_loss = torch.nn.MSELoss()

    vgg = Vgg16(requires_grad=False).to(device)

    style_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Lambda(lambda x: x.mul(255))
    ])
    style = utils.load_image(args.style_image, size=args.style_size)
    style = style_transform(style)
    style = style.repeat(args.batch_size, 1, 1, 1).to(device)
    features_style = vgg(utils.normalize_batch(style))
    gram_style = [utils.gram_matrix(y) for y in features_style]

    for e in range(args.epochs):

        #for style_batch_id, (style_x, _) in enumerate(style_loader):
        #    features_style = vgg(utils.normalize_batch(style_x.to(device)))
        #    gram_style = [utils.gram_matrix(y) for y in features_style]

            agg_content_loss = 0.
            agg_style_loss = 0.
            count = 0

            transformer.train()
            for batch_id, (x, _) in enumerate(train_loader):
                n_batch = len(x)
                count += n_batch
                optimizer.zero_grad()

                y = transformer(x.to(device))

                y = utils.normalize_batch(y)
                x = utils.normalize_batch(x).to(device)

                features_y = vgg(y)
                features_x = vgg(x)

                content_loss = args.content_weight * mse_loss(features_y.relu2_2, features_x.relu2_2)

                style_loss = 0.
                for ft_y, gm_s in zip(features_y, gram_style):
                    gm_y = utils.gram_matrix(ft_y)
                    style_loss += mse_loss(gm_y, gm_s[:n_batch, :, :])
                style_loss *= args.style_weight

                total_loss = content_loss + style_loss
                total_loss.backward()
                optimizer.step()

                agg_content_loss += content_loss.item()
                agg_style_loss += style_loss.item()

                if (batch_id + 1) % args.log_interval == 0:
                    mesg = "{}\tEpoch {}:\t[{}/{}]\tcontent: {:.6f}\tstyle: {:.6f}\ttotal: {:.6f}".format(
                        time.ctime(), e + 1, count, len(train_dataset),
                                      agg_content_loss / (batch_id + 1),
                                      agg_style_loss / (batch_id + 1),
                                      (agg_content_loss + agg_style_loss) / (batch_id + 1)
                    )
                    print(mesg)

                if args.checkpoint_model_dir is not None and (batch_id + 1) % args.checkpoint_interval == 0:
                    transformer.eval().cpu()
                    ckpt_model_filename = "ckpt_epoch_" + str(e) + "_batch_id_" + str(batch_id + 1) + ".pth"
                    ckpt_model_path = os.path.join(args.checkpoint_model_dir, ckpt_model_filename)
                    torch.save(transformer.state_dict(), ckpt_model_path)
                    transformer.to(device).train()

    # save model
    transformer.eval().cpu()
    save_model_filename = "epoch_" + str(args.epochs) + "_" + str(time.ctime()).replace(' ', '_') + "_" + str(
        args.content_weight) + "_" + str(args.style_weight) + ".model"
    save_model_path = os.path.join(args.save_model_dir, save_model_filename)
    torch.save(transformer.state_dict(), save_model_path)

    print("\nDone, trained model saved at", save_model_path)


def stylize(args):
    device = torch.device("cuda" if args.cuda else "cpu")

    img_list = open(args.content_list).readlines()
    for img in img_list:
        img = img.strip()
        save_name = img.replace("virtual3", "virtual3-style")
        if os.path.exists(save_name):
            print "exists:", save_name
            continue
        content_image = utils.load_image(img, scale=args.content_scale)
        content_transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Lambda(lambda x: x.mul(255))
        ])
        content_image = content_transform(content_image)
        content_image = content_image.unsqueeze(0).to(device)

        if args.model.endswith(".onnx"):
            output = stylize_onnx_caffe2(content_image, args)
        else:
            with torch.no_grad():
                style_model = TransformerNet()
                state_dict = torch.load(args.model)
                # remove saved deprecated running_* keys in InstanceNorm from the checkpoint
                for k in list(state_dict.keys()):
                    if re.search(r'in\d+\.running_(mean|var)$', k):
                        del state_dict[k]
                style_model.load_state_dict(state_dict)
                style_model.to(device)
                if args.export_onnx:
                    assert args.export_onnx.endswith(".onnx"), "Export model file should end with .onnx"
                    output = torch.onnx._export(style_model, content_image, args.export_onnx)
                else:
                    output = style_model(content_image).cpu()
        # save_name = img.replace("real_release", "real_release-style").strip()
        utils.save_image(save_name, output[0])

def stylize_onnx_caffe2(content_image, args):
    """
    Read ONNX model and run it using Caffe2
    """

    assert not args.export_onnx

    import onnx
    import onnx_caffe2.backend

    model = onnx.load(args.model)

    prepared_backend = onnx_caffe2.backend.prepare(model, device='CUDA' if args.cuda else 'CPU')
    inp = {model.graph.input[0].name: content_image.numpy()}
    c2_out = prepared_backend.run(inp)[0]

    return torch.from_numpy(c2_out)


def main():
    main_arg_parser = argparse.ArgumentParser(description="parser for fast-neural-style")
    subparsers = main_arg_parser.add_subparsers(title="subcommands", dest="subcommand")

    train_arg_parser = subparsers.add_parser("train", help="parser for training arguments")
    train_arg_parser.add_argument("--epochs", type=int, default=2,
                                  help="number of training epochs, default is 2")
    train_arg_parser.add_argument("--batch-size", type=int, default=4,
                                  help="batch size for training, default is 4")
    train_arg_parser.add_argument("--dataset", type=str, required=True,
                                  help="path to training dataset, the path should point to a folder "
                                       "containing another folder with all the training images")
    train_arg_parser.add_argument("--style-dataset", type=str,
                                  help="path to style image training dataset, the path should point to a folder "
                                       "containing another folder with all the training images")
    train_arg_parser.add_argument("--style-image", type=str, default="images/style-images/mosaic.jpg",
                                  help="path to style-image")
    train_arg_parser.add_argument("--save-model-dir", type=str, required=True,
                                  help="path to folder where trained model will be saved.")
    train_arg_parser.add_argument("--checkpoint-model-dir", type=str, default=None,
                                  help="path to folder where checkpoints of trained models will be saved")
    train_arg_parser.add_argument("--image-size", type=int, default=256,
                                  help="size of training images, default is 256 X 256")
    train_arg_parser.add_argument("--style-size", type=int, default=None,
                                  help="size of style-image, default is the original size of style image")
    train_arg_parser.add_argument("--cuda", type=int, required=True,
                                  help="set it to 1 for running on GPU, 0 for CPU")
    train_arg_parser.add_argument("--seed", type=int, default=42,
                                  help="random seed for training")
    train_arg_parser.add_argument("--content-weight", type=float, default=1e5,
                                  help="weight for content-loss, default is 1e5")
    train_arg_parser.add_argument("--style-weight", type=float, default=1e10,
                                  help="weight for style-loss, default is 1e10")
    train_arg_parser.add_argument("--lr", type=float, default=1e-3,
                                  help="learning rate, default is 1e-3")
    train_arg_parser.add_argument("--log-interval", type=int, default=500,
                                  help="number of images after which the training loss is logged, default is 500")
    train_arg_parser.add_argument("--checkpoint-interval", type=int, default=2000,
                                  help="number of batches after which a checkpoint of the trained model will be created")

    eval_arg_parser = subparsers.add_parser("eval", help="parser for evaluation/stylizing arguments")
    eval_arg_parser.add_argument("--content-image", type=str,
                                 help="path to content image you want to stylize")
    eval_arg_parser.add_argument("--content-list", type=str, required=True,
                                 help="path to list of content image you want to stylize")
    eval_arg_parser.add_argument("--content-scale", type=float, default=None,
                                 help="factor for scaling down the content image")
    eval_arg_parser.add_argument("--output-image", type=str,
                                 help="path for saving the output image")
    eval_arg_parser.add_argument("--model", type=str, required=True,
                                 help="saved model to be used for stylizing the image. If file ends in .pth - PyTorch path is used, if in .onnx - Caffe2 path")
    eval_arg_parser.add_argument("--cuda", type=int, required=True,
                                 help="set it to 1 for running on GPU, 0 for CPU")
    eval_arg_parser.add_argument("--export_onnx", type=str,
                                 help="export ONNX model to a given file")

    args = main_arg_parser.parse_args()

    if args.subcommand is None:
        print("ERROR: specify either train or eval")
        sys.exit(1)
    if args.cuda and not torch.cuda.is_available():
        print("ERROR: cuda is not available, try running on CPU")
        sys.exit(1)

    if args.subcommand == "train":
        check_paths(args)
        train(args)
    else:
        stylize(args)


if __name__ == "__main__":
    main()
