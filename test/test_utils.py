#
# SPDX-FileCopyrightText: Copyright (c) 2021-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
try:
    import sionna
except ImportError as e:
    import sys
    sys.path.append("../")

import unittest
import numpy as np
import tensorflow as tf

from sionna.utils.metrics import BitErrorRate, BitwiseMutualInformation, compute_ber, compute_bler, count_block_errors, count_errors
from sionna.fec.interleaving import RandomInterleaver
from sionna.utils import sim_ber, complex_normal
from sionna.fec.utils import GaussianPriorSource

gpus = tf.config.list_physical_devices('GPU')
print('Number of GPUs available :', len(gpus))
if gpus:
    gpu_num = 0 # Number of the GPU to be used
    try:
        tf.config.set_visible_devices(gpus[gpu_num], 'GPU')
        print('Only GPU number', gpu_num, 'used.')
        tf.config.experimental.set_memory_growth(gpus[gpu_num], True)
    except Runtime as e:
        print(e)


class ber_tester():
    """Utility class to emulate monte-carlo simulation with predefined
    num_errors."""
    def __init__(self, nb_errors, shape):
        self.shape = shape # shape
        self.errors = nb_errors # [1000, 400, 200, 100, 1, 0]
        self.idx = 0

    def reset(self):
        self.idx = 0

    def get_samples(self, batch_size, ebno_db):
        """Helper function to test sim_ber.

         Both inputs will be ignored but are required as placeholder to test
         sim_ber."""

        nb_errors = self.errors[self.idx]
        # increase internal counter for next call
        self.idx += 1
        x = np.zeros(np.prod(self.shape))

        # distribute nb_errors
        for i in range(nb_errors):
            x[i] = 1

        # permute
        interleaver = RandomInterleaver(axis=1, keep_batch_constant=False)
        x = tf.expand_dims(x,0)
        x = interleaver(x)
        x = tf.reshape(x, self.shape)

        return tf.zeros(self.shape), x


class TestUtils(unittest.TestCase):

    def test_ber_sim(self):
        """Test that ber_sim returns correct number of errors"""

        shape = [500, 200]
        errors = [1000, 400, 200, 100, 1, 0, 10]
        ber_true = errors / np.prod(shape)

        # init tester
        tester = ber_tester(errors, shape)

        # --- no stopping cond ---
        ber, _ = sim_ber(tester.get_samples, np.zeros_like(errors), max_mc_iter=1, early_stop=False, batch_size=1)
        # check if ber is correct
        self.assertTrue(np.allclose(ber, ber_true))

        # --- test early stopping ---
        tester.reset() # reset tester (set internal snr index to 0)

        ber, _ = sim_ber(tester.get_samples, np.zeros_like(errors), max_mc_iter=1, early_stop=True, batch_size=1)

        ber_true = errors / np.prod(shape)
        # test that all bers except last position are equal
        # last position differs as early stop triggered at 2. last point
        self.assertTrue(np.allclose(ber[:-1], ber_true[:-1]))

        # check that last ber is 0
        print(ber)
        self.assertTrue(np.allclose(ber[-1], np.zeros_like(ber[-1])))

    def test_compute_ber(self):
        """Test that compute_ber returns the correct value."""

        shape = [500, 20, 40]
        errors = [1000, 400, 200, 100, 1, 0, 10]
        bers_true = errors / np.prod(shape)

        tester = ber_tester(errors, shape)

        for _,ber in enumerate(bers_true):
            b, b_hat = tester.get_samples(0, 0)
            ber_hat = compute_ber(b, b_hat)
            self.assertTrue(np.allclose(ber, ber_hat))

    def test_count_errors(self):
        """Test that count_errors returns the correct value."""

        shape = [500, 20, 40]
        errors = [1000, 400, 200, 100, 1, 0, 10]

        tester = ber_tester(errors, shape)

        for _,e in enumerate(errors):
            b, b_hat = tester.get_samples(0, 0)
            errors_hat = count_errors(b, b_hat)
            self.assertTrue(np.allclose(e, errors_hat))

    def test_count_block_errors(self):
        """Test that count_block_errors returns the correct value."""

        shape = [50, 400]
        errors = [1000, 400, 200, 100, 1, 0, 10]

        tester = ber_tester(errors, shape)

        for _,e in enumerate(errors):
            b, b_hat = tester.get_samples(0, 0)
            bler_hat = count_block_errors(b, b_hat)

            # ground truth
            bler = 0
            for idx in range(shape[0]):
                if not np.allclose(b[idx,:], b_hat[idx,:]):
                    bler +=1

            self.assertTrue(np.allclose(bler, bler_hat))

    def test_compute_bler(self):
        """Test that compute_bler returns the correct value."""

        shape = [50, 400]
        errors = [1000, 400, 200, 100, 1, 0, 10]

        tester = ber_tester(errors, shape)

        for _,e in enumerate(errors):
            b, b_hat = tester.get_samples(0, 0)
            bler_hat = compute_bler(b, b_hat)

            # ground truth
            bler = 0
            for idx in range(shape[0]):
                if not np.allclose(b[idx,:], b_hat[idx,:]):
                    bler +=1
            bler /= shape[0]
            self.assertTrue(np.allclose(bler, bler_hat))

    def test_bit_error_metric(self):
        """Test that BitErrorRate metric returns the correct value."""

        shape = [500, 20, 40]
        errors = [1000, 400, 200, 100, 1, 0, 10]
        bers_true = errors / np.prod(shape)

        tester = ber_tester(errors, shape)

        ber_metric = BitErrorRate()

        for idx,_ in enumerate(bers_true):
            b, b_hat = tester.get_samples(0, 0)
            ber_metric(b, b_hat)
            ber_hat = ber_metric.result()
            self.assertTrue(np.allclose(np.mean(bers_true[:idx+1]),
                                        ber_hat.numpy()))

        # check that reset state also works
        ber_metric.reset_states()
        self.assertTrue(ber_metric.result().numpy()==0.)
        # test that internal counter is 0
        self.assertTrue(ber_metric.counter.numpy()==0.)

    def test_bmi_metric(self):
        """Test that BitwiseMutualInformation metric returns the correct value.

        This test uses GaussianPriorSource to generate fake LLRS with a given
        BMI.
        """

        shape = [50000, 20, 40]
        bmis = np.arange(0.1, 0.9, 0.1)

        bmi_metric = BitwiseMutualInformation()
        source = GaussianPriorSource(specified_by_mi=True)

        for idx, bmi in enumerate(bmis):
            # generate fake llrs with given bmi
            llr = source([shape, bmi])
            b = tf.zeros_like(llr)
            # update metric
            bmi_metric(b, llr)

            self.assertTrue(np.allclose(np.mean(bmis[:idx+1]),
                                        bmi_metric.result().numpy(),
                                        rtol=0.01))

        # check that reset state also works
        bmi_metric.reset_states()
        self.assertTrue(bmi_metric.result().numpy()==0.)
        # test that internal counter is 0
        self.assertTrue(bmi_metric.counter.numpy()==0.)



class TestComplexNormal(unittest.TestCase):
    """Test cases for the complex_normal function"""
    def test_variance(self):
        shape = [100000000]
        v = [0, 0.5, 1.0, 2.3, 25]
        for var in v:
            x = complex_normal(shape, var)
            self.assertTrue(np.allclose(var, np.var(x), rtol=1e-3))
            self.assertTrue(np.allclose(np.var(np.real(x)), np.var(np.imag(x)), rtol=1e-3))

        # Default variance
        var_hat = np.var(complex_normal(shape))
        self.assertTrue(np.allclose(1.0, var_hat, rtol=1e-3))

    def test_dtype(self):
        for dtype in [tf.complex64, tf.complex128]:
            x = complex_normal([100], dtype=dtype)
            self.assertEqual(dtype, x.dtype)

    def test_dims(self):
        dims = [
                [100],
                [7, 8, 5],
                [4, 5, 67, 8]
                ]
        for d in dims:
            x = complex_normal(d)
            self.assertEqual(d, x.shape)

    def test_xla(self):
        @tf.function(jit_compile=True)
        def func(batch_size, var):
            return complex_normal([batch_size, 1000], var, tf.complex128)
        var = 0.3
        var_hat = np.var(func(100000, var))
        self.assertTrue(np.allclose(var, var_hat, rtol=1e-3))

        var = 1
        var_hat = np.var(func(100000, var))
        self.assertTrue(np.allclose(var, var_hat, rtol=1e-3))

        var = tf.cast(0.3, tf.int32)
        var_hat = np.var(func(100000, var))
        self.assertTrue(np.allclose(var, var_hat, rtol=1e-3))
