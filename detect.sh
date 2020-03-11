#net=psmnet
#model=models/psmnet/model_best.pth

dataset=irs
net=normnets

#model=models/dispnetcres/fadnet.pth
#model=models/fadnet_sf.pth
#model=models/normnets-sf-fl1050-n21.128.pth
#model=models/dispnormnet-sf-fl1050-d0.928-n16.727.pth
#model=models/dtonnet-sf-fl1050-d0.921-n15.6.pth
#model=models/dnfusionnet-sf-fl1050-d0.926-n16.192.pth
#model=models/dispnetc-sf-fl1050-d1.09.pth
#model=models/dtonfusionnet-sf-fl1050-d0.973-n15.768.pth
#model=models/dispnetc-irs/dispnetc_2_19.pth
#model=models/dtonnet-irs/dtonnet_2_19.pth
model=models/normnets-irs/normnets_0_29.pth
outf=detect_results/${net}-${dataset}/

filelist=lists/IRSDataset_TEST.list
#filelist=lists/SHAOHUAI_CLEAN_FlyingThings3D_release_TEST.list
#filelist=lists/${dataset}_test.list
#filelist=lists/FlyingThings3D_release_TEST_norm.list
#filelist=lists/real_d435i.list
filepath=data

CUDA_VISIBLE_DEVICES=2 python detecter.py --model $model --rp $outf --filelist $filelist --filepath $filepath --devices 0 --net ${net} --norm-on 
