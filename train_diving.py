import argparse
import logging
import os
from os import listdir
from os.path import isfile, join, isdir

import torch
import torch.nn as nn
from torch import cuda
from torch.autograd import Variable
from torch.utils.data import DataLoader,Dataset
from torch.utils.data.dataset import Dataset

import torchvision
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils

#import matplotlib.pyplot as plt
import numpy as np
import random
from PIL import Image
# import cv2
from torchvision.transforms import ToPILImage
from torch.optim.lr_scheduler import StepLR
from p3d_model import P3D199, C3D, get_optim_policies
from utils import transfer_model
from dataset import divingDataset
#from visualize import make_dot
from scipy.stats import spearmanr

logging.basicConfig(
	format='%(asctime)s %(levelname)s: %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)

parser = argparse.ArgumentParser(description="Diving")

parser.add_argument("--load", default=0, type=int,
					help="Load saved network weights. 0 represent don't load; other number represent the model number")
parser.add_argument("--save", default=0, type=int,
					help="Save network weights. 0 represent don't save; number represent model number")  
parser.add_argument("--epochs", default=65, type=int,
					help="Epochs through the data. (default=60)")  
parser.add_argument("--learning_rate", "-lr", default=0.001, type=float,
					help="Learning rate of the optimization. (default=0.001)")              
parser.add_argument("--batch_size", default=8, type=int,
					help="Batch size for training. (default=16)")
parser.add_argument("--optimizer", default="SGD", choices=["SGD", "Adadelta", "Adam"],
					help="Optimizer of choice for training. (default=Adam)")
parser.add_argument("--gpuid", default=[], nargs='+', type=str,
					help="ID of gpu device to use. Empty implies cpu usage.")
parser.add_argument("--size", default=160, type=int,
					help="size of images.")
parser.add_argument("--machine", default='ye_home', type=str,
					help="which machine to run the code. choice from ye_home and marcc")
parser.add_argument("--only_last_layer", default=0, type=int,
					help="whether choose to freezen the parameters for all the layers except the linear layer on the pre-trained model")
parser.add_argument("--normalize", default=1, type=int,
					help="do the normalize for the images")
parser.add_argument("--lr_steps", default=[30,60], type=int, nargs="+",
					help="steps to decay learning rate")
parser.add_argument("--use_policy", default=0, type=int,
					help="policy for getting decay of learning rate")
parser.add_argument("--use_trained_model", default=1, type=int,
					help="whether use the pre-trained model on kinetics or not")
parser.add_argument("--model", default="P3D",  choices=["P3D", "C3D","I3D"],
					help="which machine to run the code. choice from ye_home and marcc")
parser.add_argument("--random", default=False,  type=bool,
					help="random sapmling in training")


def adjust_learning_rate(optimizer, epoch, lr_steps):
	"""Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
	decay = 0.1 ** (sum(epoch >= np.array(lr_steps)))
	lr = options.learning_rate * decay
	for param_group in optimizer.param_groups:
		param_group['lr'] = lr * param_group['lr_mult']
		param_group['weight_decay'] = decay * param_group['decay_mult']


def main(options):
	# Path configuration


	machine =options.machine

	# Path to the directories of features and labels
	if machine == 'ye_home':
		train_file = '/home/ye/Works/diving-score/training_idx.npy'
		test_file = '/home/ye/Works/diving-score/testing_idx.npy'
		data_folder = '/home/ye/Works/diving-score/frames'
		label_file = '/home/ye/Works/diving-score/overall_scores.npy'

	elif machine == 'peterchin':
		train_file = '/data/xiang/diving-score/testing_idx.npy'
		test_file = '/data/xiang/diving-score/testing_idx.npy'
		data_folder = '/data/xiang/diving-score/frames'            	
		label_file = '/data/xiang/diving-score/overall_scores.npy' 
	
	# elif machine == 'marcc':
	# 	train_file = './ucfTrainTestlist/trainlist0'+str(split)+'.txt'
	# 	test_file = './ucfTrainTestlist/testlist0'+str(split)+'.txt'
	# 	data_folder = './frames'
	# 	label_file = './ucfTrainTestlist/classInd.txt'

	if options.model=="C3D":
		options.size = 112
	if options.model=="I3D":
		options.size = 224
		
	if options.normalize:
		transformations = transforms.Compose([transforms.Scale((options.size,options.size)),
										transforms.ToTensor(),
										transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
										])
	else:
		transformations = transforms.Compose([transforms.Scale((options.size,options.size)),
										transforms.ToTensor()
										])	
	
	dset_train = divingDataset(data_folder, train_file, label_file, transformations, random, size=options.size)

	dset_test = divingDataset(data_folder, test_file, label_file, transformations, random, size=options.size)

	train_loader = DataLoader(dset_train,
							  batch_size = options.batch_size,
							  shuffle = True,
							 )

	test_loader = DataLoader(dset_test,
							 batch_size = options.batch_size,
							 shuffle = False,
							 )

	use_cuda = (len(options.gpuid) >= 1)
	#if options.gpuid:
		#cuda.set_device(int(options.gpuid[0]))
	
	# Initial the model
	if options.model=="P3D":
		if options.use_trained_model:
			model = P3D199(pretrained=True,num_classes=400)
		else:
			model = P3D199(pretrained=False,num_classes=400)
	elif options.model=="C3D":
		if options.use_trained_model:
			model = C3D()
			model.load_state_dict(torch.load('c3d.pickle'))
		else:
			model = C3D()
	elif options.model=="I3D":
		if options.use_trained_model:
			model = I3D(num_classes=400, modality='rgb')
			model.load_state_dict(torch.load('model_rgb.pth'))
		else:
			model = I3D(num_classes=101, modality='rgb')
	else:
		logging.error("No such model: {0}".format(options.model))


	if options.only_last_layer:
		for param in model.parameters():
			param.requires_grad = False

	model = transfer_model(model,num_classes=1, model_type=options.model)
	# logging.info("fc size is: {0}".format(model.fc))

	if use_cuda > 0:
		model.cuda()

	start_epoch = 0

	if options.load:
		logging.info("=> loading checkpoint"+str(options.load)+".tar")
		checkpoint = torch.load('checkpoint'+str(options.load)+'.tar')
		start_epoch = checkpoint['epoch']
		model.load_state_dict(checkpoint['state_dict'])


	# criterion = torch.nn.CrossEntropyLoss()
	criterion = nn.MSELoss()

	if options.only_last_layer:
		optimizer = eval("torch.optim." + options.optimizer)(model.fc.parameters(), lr=options.learning_rate)
	else:
		if options.optimizer=="SGD":
			if options.use_policy:
				policies = get_optim_policies(model=model,modality='RGB',enable_pbn=True)
				optimizer = torch.optim.SGD(policies,
											options.learning_rate,
											momentum=0.9,
											weight_decay=5e-4)
			else:
				optimizer = torch.optim.SGD(model.parameters(),
							options.learning_rate,
							momentum=0.9,
							weight_decay=5e-4)
		else:
			optimizer = eval("torch.optim." + options.optimizer)(model.parameters(), lr=options.learning_rate)

	scheduler = StepLR(optimizer, step_size=options.lr_steps[0], gamma=0.1)

	# main training loop
	# last_dev_avg_loss = float("inf")
	for epoch_i in range(start_epoch, options.epochs):
		logging.info("At {0}-th epoch.".format(epoch_i))
		
		if len(options.lr_steps)>0 and options.use_policy and options.optimizer=="SGD":
				adjust_learning_rate(optimizer, epoch_i, options.lr_steps)
		else:
			scheduler.step()

		train_loss = 0.0
		all_train_output = []
		all_labels = []
		for it, train_data in enumerate(train_loader, 0):
			vid_tensor, labels = train_data

			if use_cuda:
				vid_tensor, labels = Variable(vid_tensor).cuda(),  Variable(labels).cuda()
			else:
				vid_tensor, labels = Variable(vid_tensor), Variable(labels)

			model.train()

			if options.model == "I3D" or options.model == "P3D":
				train_output = model(vid_tensor)
				train_output = train_output[0]
			else:
				train_output = model(vid_tensor)

			all_train_output = np.append(all_train_output, train_output.data.cpu().numpy()[:,0])
			all_labels = np.append(all_labels, labels.data.cpu().numpy())

			# print all_train_output, all_labels
			loss = criterion(train_output, labels)
			train_loss += loss.data[0]
			#if it%1 == 0:
			#	print (train_output.data.cpu().numpy()[0][0]), ('-'), (labels.data.cpu().numpy()[0]),('  ') ,(train_output.data.cpu().numpy()[1][0]), ('-') ,(labels.data.cpu().numpy()[1])
			logging.info("loss at batch {0}: {1}".format(it, loss.data[0]))
			# logging.debug("loss at batch {0}: {1}".format(it, loss.data[0]))
			optimizer.zero_grad()
			loss.backward()
			optimizer.step()

			
		train_avg_loss = train_loss / (len(dset_train) / options.batch_size)
		rho, p_val = spearmanr(all_train_output, all_labels)
		logging.info("Average training loss value per instance is {0}, the corr is {1} at the end of epoch {2}".format(train_avg_loss, rho, epoch_i))

		if options.save:
			torch.save({
				'epoch': epoch_i + 1,
				'state_dict': model.state_dict(),
				'optimizer' : optimizer.state_dict(),
				}, 'checkpoint'+str(options.save)+'.tar' )


		# # main test loop
		model.eval()
		test_loss = 0.0
		all_test_output = []
		all_labels = []
		for it, test_data in enumerate(test_loader, 0):
			vid_tensor, labels = test_data
			if use_cuda:
				vid_tensor, labels = Variable(vid_tensor).cuda(),  Variable(labels).cuda()
			else:
				vid_tensor, labels = Variable(vid_tensor), Variable(labels)

			if options.model == "I3D" or options.model == "P3D":
				test_output = model(vid_tensor)
				test_output = test_output[0]
			else:
				test_output = model(vid_tensor)

			all_test_output = np.append(all_test_output, test_output.data.cpu().numpy()[:,0])
			all_labels = np.append(all_labels, labels.data.cpu().numpy())

			loss = criterion(test_output, labels)
			test_loss += loss.data[0]

			logging.info("loss at batch {0}: {1}".format(it, loss.data[0]))

		test_avg_loss = test_loss / (len(dset_test) / options.batch_size)
		# logging.info("Average test loss value per instance is {0}".format(test_avg_loss))

		rho, p_val = spearmanr(all_test_output, all_labels)
		logging.info("Average test loss value per instance is {0}, the corr is {1} at the end of epoch {2}".format(test_avg_loss, rho, epoch_i))
#######################################################################################################################
		# the last test for visualization
		model.eval()
		test_loss = 0.0
		all_test_output = []
		all_labels = []
		for it, test_data in enumerate(test_loader, 0):
			vid_tensor, labels = test_data
			if use_cuda:
				vid_tensor, labels = Variable(vid_tensor).cuda(),  Variable(labels).cuda()
			else:
				vid_tensor, labels = Variable(vid_tensor), Variable(labels)

			if options.model == "I3D" or options.model == "P3D":
				test_output = model(vid_tensor)
				test_output = test_output[0]
			else:
				test_output = model(vid_tensor)

			all_test_output = np.append(all_test_output, test_output.data.cpu().numpy()[:,0])
			all_labels = np.append(all_labels, labels.data.cpu().numpy())

			loss = criterion(test_output, labels)
			test_loss += loss.data[0]

			print (test_output.data.cpu().numpy()[0][0]), ('-'), (labels.data.cpu().numpy()[0])
			logging.info("loss at batch {0}: {1}".format(it, loss.data[0]))

		test_avg_loss = test_loss / (len(dset_test) / options.batch_size)
		# logging.info("Average test loss value per instance is {0}".format(test_avg_loss))

		rho, p_val = spearmanr(all_test_output, all_labels)
		logging.info("Average test loss value per instance is {0}, the corr is {1} at the end of epoch {2}".format(test_avg_loss, rho, epoch_i))

if __name__ == "__main__":
	ret = parser.parse_known_args()
	options = ret[0]
	if ret[1]:
		logging.warning("unknown arguments: {0}".format(parser.parse_known_args()[1]))
	main(options)