import torch
from torch.autograd import Variable
import torch.nn.functional as F


def evaluateModel(model, loss_func, testloader):
    model.eval()  # Set to testing mode
    cnt = 0
    loss_sum = 0
    targets = []
    predictions = []
    for data in testloader:
        points, target = data
        points = Variable(points,volatile=True)
        target = Variable(target,volatile=True)
        points = points.transpose(2, 1)
        prediction = model(points).view(-1)
        loss = loss_func(prediction, target)
        cnt += target.size(0)
        loss_sum += loss.data[0]
        predictions.append(prediction)
        targets.append(target)
    return loss_sum / cnt, torch.cat(targets), torch.cat(predictions)