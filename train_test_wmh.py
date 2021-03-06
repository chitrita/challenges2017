from __future__ import print_function
import argparse
import os
import sys
from time import strftime
import numpy as np
from nibabel import load as load_nii
from utils import color_codes
from data_creation import get_cnn_centers, load_norm_list, get_patches_list
from data_creation import load_patches_ganseg_by_batches, load_patches_gandisc_by_batches
from data_manipulation.generate_features import get_mask_voxels
from data_manipulation.metrics import dsc_seg, probabilistic_dsc_seg
from nets import get_wmh_nets
import keras.backend as K


def parse_inputs():
    # I decided to separate this function, for easier acces to the command line parameters
    parser = argparse.ArgumentParser(description='Test different nets with 3D data.')
    parser.add_argument('-f', '--training-folder', dest='dir_train', default='/home/mariano/DATA/WMHTrain/')
    parser.add_argument('-F', '--test-folder', dest='dir_test', default='/home/mariano/DATA/WMHTest/')
    parser.add_argument('-i', '--patch-width', dest='patch_width', type=int, default=15)
    parser.add_argument('-k', '--kernel-size', dest='conv_width', nargs='+', type=int, default=3)
    parser.add_argument('-c', '--conv-blocks', dest='conv_blocks', type=int, default=4)
    parser.add_argument('-b', '--batch-size', dest='batch_size', type=int, default=128)
    parser.add_argument('-B', '--batch-test-size', dest='test_size', type=int, default=32768)
    parser.add_argument('-d', '--dense-size', dest='dense_size', type=int, default=256)
    parser.add_argument('-D', '--down-sampling', dest='downsample', type=int, default=1)
    parser.add_argument('-n', '--num-filters', action='store', dest='n_filters', nargs='+', type=int, default=[32])
    parser.add_argument('-e', '--epochs', action='store', dest='epochs', type=int, default=5)
    parser.add_argument('-r', '--swap-rate', action='store', dest='swap_rate', type=float, default=0.5)
    parser.add_argument('-u', '--unbalanced', action='store_false', dest='balanced', default=True)
    parser.add_argument('-p', '--preload', action='store_true', dest='preload', default=False)
    parser.add_argument('-s', '--shuffle-labels', action='store_true', dest='shuffle', default=False)
    parser.add_argument('-P', '--patience', dest='patience', type=int, default=2)
    parser.add_argument('--flair', action='store', dest='flair', default='pre/FLAIR.nii.gz')
    parser.add_argument('--t1', action='store', dest='t1', default='pre/T1.nii.gz')
    parser.add_argument('--labels', action='store', dest='labels', default='wmh.nii.gz')
    return vars(parser.parse_args())


def get_names_from_path(options, train=True):
    path = options['dir_train'] if train else options['dir_test']

    directories = filter(os.path.isdir, [os.path.join(path, f) for f in os.listdir(path)])
    patients = sorted(directories)

    # Prepare the names
    flair_names = [os.path.join(path, p, options['flair']) for p in patients]
    t1_names = [os.path.join(path, p, options['t1']) for p in patients]

    label_names = np.array([os.path.join(path, p, options['labels']) for p in patients])
    image_names = np.stack(filter(None, [flair_names, t1_names]), axis=1)

    return image_names, label_names


def train_nets(gan, gan_dsc, cnn, cnn_dsc, p, x, y, name, adversarial_w):
    options = parse_inputs()
    c = color_codes()
    # Data stuff
    patient_path = '/'.join(p[0].rsplit('/')[:-1])
    train_data, train_labels = get_names_from_path(options)
    # Prepare the net hyperparameters
    epochs = options['epochs']
    patch_width = options['patch_width']
    patch_size = (patch_width, patch_width, patch_width)
    preload = options['preload']
    batch_size = options['batch_size']

    print('%s[%s]    %sTraining the networks%s (%sCNN%s vs %sGAN%s: %s%s%s/%s%d%s parameters)' % (
        c['c'], strftime("%H:%M:%S"),
        c['g'], c['nc'],
        c['lgy'], c['nc'],
        c['y'], c['nc'],
        c['b'], gan.count_params(), c['nc'],
        c['b'], cnn.count_params(), c['nc']
    ))

    net_name = os.path.join(patient_path, name)
    checkpoint_name = os.path.join(patient_path, net_name + '.weights')

    try:
        gan.load_weights(checkpoint_name + '.gan.e%d' % epochs)
        gan_dsc.load_weights(checkpoint_name + '.gan-dsc.e%d' % epochs)
        cnn.load_weights(checkpoint_name + '.net.e%d' % epochs)
        cnn_dsc.load_weights(checkpoint_name + '.net-dsc.e%d' % epochs)
    except IOError:
        x_disc, y_disc = load_patches_gandisc_by_batches(
            source_names=train_data,
            target_names=[p],
            n_centers=len(x),
            size=patch_size,
            preload=preload,
        )
        print('%s[%s]%s     %sStarting the training process%s' % (
            c['c'], strftime("%H:%M:%S"), c['nc'],
            c['g'], c['nc']
        ))
        for e in range(epochs):
            print(' '.join([''] * 16) + c['g'] + 'Epoch ' +
                  c['b'] + '%d' % (e + 1) + c['nc'] + c['g'] + '/%d' % epochs + c['nc'])
            try:
                cnn.load_weights(checkpoint_name + '.net.e%d' % (e + 1))
                cnn_dsc.load_weights(checkpoint_name + '.net-dsc.e%d' % (e + 1))
                gan.load_weights(checkpoint_name + '.gan.e%d' % (e + 1))
                gan_dsc.load_weights(checkpoint_name + '.gan-dsc.e%d' % (e + 1))
            except IOError:
                print(c['lgy'], end='\r')
                cnn.fit(x, y, batch_size=batch_size, epochs=1)
                cnn_dsc.fit(x, y, batch_size=batch_size, epochs=1)
                print(c['y'], end='\r')
                gan.fit([x, x_disc], [y, y_disc], batch_size=batch_size, epochs=1)
                gan_dsc.fit([x, x_disc], [y, y_disc], batch_size=batch_size, epochs=1)
                print(c['nc'], end='\r')

                cnn.save_weights(checkpoint_name + '.net.e%d' % (e + 1))
                cnn_dsc.save_weights(checkpoint_name + '.net-dsc.e%d' % (e + 1))
                gan.save_weights(checkpoint_name + '.gan.e%d' % (e + 1))
                gan_dsc.save_weights(checkpoint_name + '.gan-dsc.e%d' % (e + 1))

            adversarial_weight = min([K.eval(adversarial_w) + 0.1, 1.0])
            K.set_value(adversarial_w, adversarial_weight)


def test_net(net, p, outputname):

    c = color_codes()
    options = parse_inputs()
    patch_width = options['patch_width']
    patch_size = (patch_width, patch_width, patch_width)
    batch_size = options['test_size']
    p_name = p[0].rsplit('/')[-2]
    patient_path = '/'.join(p[0].rsplit('/')[:-1])
    outputname_path = os.path.join(patient_path, outputname + '.nii.gz')
    pr_outputname_path = os.path.join(patient_path, outputname + '.pr.nii.gz')
    try:
        image = load_nii(outputname_path).get_data()
    except IOError:
        print('%s[%s]    %sTesting the network%s' % (c['c'], strftime("%H:%M:%S"), c['g'], c['nc']))
        nii = load_nii(p[0])
        roi = nii.get_data().astype(dtype=np.bool)
        centers = get_mask_voxels(roi)
        test_samples = np.count_nonzero(roi)
        image = np.zeros_like(roi).astype(dtype=np.uint8)
        pr = np.zeros_like(roi).astype(dtype=np.float32)
        print('%s[%s]    %s<Creating the probability map %s%s%s%s - %s%s%s%s (%d samples)>%s' % (
            c['c'], strftime("%H:%M:%S"),
            c['g'], c['b'], p_name, c['nc'],
            c['g'], c['b'], outputname, c['nc'],
            c['g'], test_samples, c['nc']
        ))

        n_centers = len(centers)
        image_list = [load_norm_list(p)]

        for i in range(0, n_centers, batch_size):
            print(
                '%f%% tested (step %d/%d)' % (100.0 * i / n_centers, (i / batch_size) + 1, -(-n_centers/batch_size)),
                end='\r'
            )
            sys.stdout.flush()
            centers_i = [centers[i:i + batch_size]]
            x = get_patches_list(image_list, centers_i, patch_size, True)
            x = np.concatenate(x).astype(dtype=np.float32)
            y_pr_pred = net.predict(x, batch_size=options['batch_size'])

            [x, y, z] = np.stack(centers_i[0], axis=1)

            # We store the results
            image[x, y, z] = np.argmax(y_pr_pred, axis=1).astype(dtype=np.int8)
            pr[x, y, z] = y_pr_pred[:, 1].astype(dtype=np.float32)

        print(' '.join([''] * 50), end='\r')
        sys.stdout.flush()

        # Post-processing (Basically keep the biggest connected region)
        # image = get_biggest_region(image)
        print('%s                   -- Saving image %s%s%s' % (c['g'], c['b'], outputname_path, c['nc']))

        nii.get_data()[:] = image
        nii.to_filename(outputname_path)
        nii.get_data()[:] = pr
        nii.to_filename(pr_outputname_path)
    return image


def main():
    options = parse_inputs()
    c = color_codes()

    # Prepare the net hyperparameters
    epochs = options['epochs']
    patch_width = options['patch_width']
    patch_size = (patch_width, patch_width, patch_width)
    dense_size = options['dense_size']
    conv_blocks = options['conv_blocks']
    n_filters = options['n_filters']
    filters_list = n_filters if len(n_filters) > 1 else n_filters * conv_blocks
    conv_width = options['conv_width']
    kernel_size_list = conv_width if isinstance(conv_width, list) else [conv_width] * conv_blocks
    balanced = options['balanced']
    # Data loading parameters
    downsample = options['downsample']
    preload = options['preload']
    shuffle = options['shuffle']

    # Prepare the sufix that will be added to the results for the net and images
    filters_s = 'n'.join(['%d' % nf for nf in filters_list])
    conv_s = 'c'.join(['%d' % cs for cs in kernel_size_list])
    unbalanced_s = '.ub' if not balanced else ''
    shuffle_s = '.s' if shuffle else ''
    params_s = (unbalanced_s, shuffle_s, patch_width, conv_s, filters_s, dense_size, downsample)
    sufix = '%s%s.p%d.c%s.n%s.d%d.D%d' % params_s
    preload_s = ' (with %spreloading%s%s)' % (c['b'], c['nc'], c['c']) if preload else ''

    print('%s[%s] Starting training%s%s' % (c['c'], strftime("%H:%M:%S"), preload_s, c['nc']))
    train_data, _ = get_names_from_path(options)
    test_data, test_labels = get_names_from_path(options, False)

    input_shape = (train_data.shape[1],) + patch_size

    dsc_results = list()
    dsc_results_pr = list()

    train_data, train_labels = get_names_from_path(options)
    centers_s = np.random.permutation(
        get_cnn_centers(train_data[:, 0], train_labels, balanced=balanced)
    )[::downsample]
    x_seg, y_seg = load_patches_ganseg_by_batches(
        image_names=train_data,
        label_names=train_labels,
        source_centers=centers_s,
        size=patch_size,
        nlabels=2,
        preload=preload,
    )

    for i, (p, gt_name) in enumerate(zip(test_data, test_labels)):
        p_name = p[0].rsplit('/')[-3]
        patient_path = '/'.join(p[0].rsplit('/')[:-1])
        print('%s[%s] %sCase %s%s%s%s%s (%d/%d):%s' % (
            c['c'], strftime("%H:%M:%S"), c['nc'],
            c['c'], c['b'], p_name, c['nc'],
            c['c'], i + 1, len(test_data), c['nc']
        ))

        # NO DSC objective
        image_cnn_name = os.path.join(patient_path, p_name + '.cnn.test%s.e%d' % (shuffle_s, epochs))
        image_gan_name = os.path.join(patient_path, p_name + '.gan.test%s.e%d' % (shuffle_s, epochs))
        # DSC objective
        image_cnn_dsc_name = os.path.join(patient_path, p_name + '.dsc-cnn.test%s.e%d' % (shuffle_s, epochs))
        image_gan_dsc_name = os.path.join(patient_path, p_name + '.dsc-gan.test%s.e%d' % (shuffle_s, epochs))
        try:
            # NO DSC objective
            image_cnn = load_nii(image_cnn_name + '.nii.gz').get_data()
            image_cnn_pr = load_nii(image_cnn_name + '.pr.nii.gz').get_data()
            image_gan = load_nii(image_gan_name + '.nii.gz').get_data()
            image_gan_pr = load_nii(image_gan_name + '.pr.nii.gz').get_data()
            # DSC objective
            image_cnn_dsc = load_nii(image_cnn_dsc_name + '.nii.gz').get_data()
            image_cnn_dsc_pr = load_nii(image_cnn_dsc_name + '.pr.nii.gz').get_data()
            image_gan_dsc = load_nii(image_gan_dsc_name + '.nii.gz').get_data()
            image_gan_dsc_pr = load_nii(image_gan_dsc_name + '.pr.nii.gz').get_data()
        except IOError:
            # Lesion segmentation
            adversarial_w = K.variable(0)
            # NO DSC objective
            cnn, gan, gan_test = get_wmh_nets(
                input_shape=input_shape,
                filters_list=filters_list,
                kernel_size_list=kernel_size_list,
                dense_size=dense_size,
                lambda_var=adversarial_w
            )
            # DSC objective
            cnn_dsc, gan_dsc, gan_dsc_test = get_wmh_nets(
                input_shape=input_shape,
                filters_list=filters_list,
                kernel_size_list=kernel_size_list,
                dense_size=dense_size,
                lambda_var=adversarial_w,
                dsc_obj=True
            )
            train_nets(
                gan=gan,
                gan_dsc=gan_dsc,
                cnn=cnn,
                cnn_dsc=cnn_dsc,
                p=p,
                x=x_seg,
                y=y_seg,
                name='wmh2017' + sufix,
                adversarial_w=adversarial_w
            )
            # NO DSC objective
            image_cnn = test_net(cnn, p, image_cnn_name)
            image_cnn_pr = load_nii(image_cnn_name + '.pr.nii.gz').get_data()
            image_gan = test_net(gan_test, p, image_gan_name)
            image_gan_pr = load_nii(image_gan_name + '.pr.nii.gz').get_data()
            # DSC objective
            image_cnn_dsc = test_net(cnn_dsc, p, image_cnn_dsc_name)
            image_cnn_dsc_pr = load_nii(image_cnn_dsc_name + '.pr.nii.gz').get_data()
            image_gan_dsc = test_net(gan_dsc_test, p, image_gan_dsc_name)
            image_gan_dsc_pr = load_nii(image_gan_dsc_name + '.pr.nii.gz').get_data()
        # NO DSC objective
        seg_cnn = image_cnn.astype(np.bool)
        seg_gan = image_gan.astype(np.bool)
        # DSC objective
        seg_cnn_dsc = image_cnn_dsc.astype(np.bool)
        seg_gan_dsc = image_gan_dsc.astype(np.bool)

        seg_gt = load_nii(gt_name).get_data()
        not_roi = np.logical_not(seg_gt == 2)

        results_cnn_dsc = dsc_seg(seg_gt == 1, np.logical_and(seg_cnn_dsc, not_roi))
        results_cnn_dsc_pr = probabilistic_dsc_seg(seg_gt == 1, image_cnn_dsc_pr * not_roi)
        results_cnn = dsc_seg(seg_gt == 1, np.logical_and(seg_cnn, not_roi))
        results_cnn_pr = probabilistic_dsc_seg(seg_gt == 1, image_cnn_pr * not_roi)

        results_gan_dsc = dsc_seg(seg_gt == 1, np.logical_and(seg_gan_dsc, not_roi))
        results_gan_dsc_pr = probabilistic_dsc_seg(seg_gt == 1, image_gan_dsc_pr * not_roi)
        results_gan = dsc_seg(seg_gt == 1, np.logical_and(seg_gan, not_roi))
        results_gan_pr = probabilistic_dsc_seg(seg_gt == 1, image_gan_pr * not_roi)

        whites = ''.join([' '] * 14)
        print('%sCase %s%s%s%s %sCNN%s vs %sGAN%s DSC: %s%f%s (%s%f%s) vs %s%f%s (%s%f%s)' % (
            whites, c['c'], c['b'], p_name, c['nc'],
            c['lgy'], c['nc'],
            c['y'], c['nc'],
            c['lgy'], results_cnn_dsc, c['nc'],
            c['lgy'], results_cnn, c['nc'],
            c['y'], results_gan_dsc, c['nc'],
            c['y'], results_gan, c['nc']
        ))
        print('%sCase %s%s%s%s %sCNN%s vs %sGAN%s DSC Pr: %s%f%s (%s%f%s) vs %s%f%s (%s%f%s)' % (
            whites, c['c'], c['b'], p_name, c['nc'],
            c['lgy'], c['nc'],
            c['y'], c['nc'],
            c['lgy'], results_cnn_dsc_pr, c['nc'],
            c['lgy'], results_cnn_pr, c['nc'],
            c['y'], results_gan_dsc_pr, c['nc'],
            c['y'], results_gan_pr, c['nc']
        ))

        dsc_results.append((results_cnn_dsc, results_cnn, results_gan_dsc, results_gan))
        dsc_results_pr.append((results_cnn_dsc_pr, results_cnn_pr, results_gan_dsc_pr, results_gan_pr))

    final_dsc = tuple(np.mean(dsc_results, axis=0))
    final_dsc_pr = tuple(np.mean(dsc_results_pr, axis=0))
    print('Final results DSC: %s%f%s (%s%f%s) vs %s%f%s (%s%f%s)' % (
        c['lgy'], final_dsc[0], c['nc'],
        c['lgy'], final_dsc[1], c['nc'],
        c['y'], final_dsc[2], c['nc'],
        c['y'], final_dsc[3], c['nc']
    ))
    print('Final results DSC Pr: %s%f%s (%s%f%s) vs %s%f%s (%s%f%s)' % (
        c['lgy'], final_dsc_pr[0], c['nc'],
        c['lgy'], final_dsc_pr[1], c['nc'],
        c['y'], final_dsc_pr[2], c['nc'],
        c['y'], final_dsc_pr[3], c['nc']
    ))

if __name__ == '__main__':
    main()
