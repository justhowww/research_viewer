"""
Event utilities for H5 file visualization in the viewer.
Copied and adapted from utils/event_utils for self-contained use.
"""
import h5py
import numpy as np


def v2e_to_monash(hdf_path):
    """
    Convert v2e format H5 to Monash-style dict.
    v2e format: f['events'] with columns [timestamp, x, y, polarity]

    @param hdf_path Path to v2e H5 file
    @returns Events dict in Monash format with 'xs', 'ys', 'ts', 'ps'
    """
    with h5py.File(hdf_path, 'r') as f:
        data = f['events'][()]  # shape: (N, 4)
        # v2e order: [timestamp, x, y, polarity]
        events = {
            'ts': data[:, 0],
            'xs': data[:, 1],
            'ys': data[:, 2],
            'ps': np.where(data[:, 3] > 0, 1, -1)
        }

    events['height'] = int(np.max(events['ys'])) + 1
    events['width'] = int(np.max(events['xs'])) + 1
    return events


def read_h5_events_dict(hdf_path, read_frames=False):
    """
    Read events from HDF5 file. Supports both Monash and v2e formats.
    @param hdf_path Path to HDF5 file
    @param read_frames Whether to read associated frames (default False for viewer)
    @returns Events as a dict with entries 'xs', 'ys', 'ts', 'ps'
    """
    f = h5py.File(hdf_path, 'r')

    # Check format and dispatch
    if 'events' in f and isinstance(f['events'], h5py.Dataset):
        # v2e format: single 'events' dataset with columns [t, x, y, p]
        f.close()
        return v2e_to_monash(hdf_path)

    # Monash format variants
    if 'events/x' in f:
        # legacy Monash format
        events = {
            'xs': f['events/x'][:],
            'ys': f['events/y'][:],
            'ts': f['events/ts'][:],
            'ps': np.where(f['events/p'][:], 1, -1)
        }
    elif 'events/xs' in f:
        # standard Monash format
        events = {
            'xs': f['events/xs'][:],
            'ys': f['events/ys'][:],
            'ts': f['events/ts'][:],
            'ps': np.where(f['events/ps'][:], 1, -1)
        }
    else:
        f.close()
        raise ValueError(f"Unknown H5 format. Keys: {list(f.keys())}")

    # Get sensor size from data
    events['height'] = int(np.max(events['ys'])) + 1
    events['width'] = int(np.max(events['xs'])) + 1

    if read_frames and 'images' in f:
        images = []
        image_stamps = []
        for key in f['images']:
            frame = f['images/{}'.format(key)][:]
            images.append(frame)
            image_stamps.append(f['images/{}'.format(key)].attrs['timestamp'])
        events['frames'] = images
        events['frame_timestamps'] = np.array(image_stamps)

    f.close()
    return events


def events_to_image(xs, ys, ps, sensor_size):
    """
    Accumulate events into an image.
    @param xs x coordinates
    @param ys y coordinates
    @param ps polarities/weights
    @param sensor_size (height, width) tuple
    @returns Accumulated event image
    """
    img_size = (sensor_size[0] + 1, sensor_size[1] + 1)
    coords = np.stack((ys.astype(int), xs.astype(int)))

    # Clip coordinates to valid range
    coords[0] = np.clip(coords[0], 0, img_size[0] - 1)
    coords[1] = np.clip(coords[1], 0, img_size[1] - 1)

    abs_coords = np.ravel_multi_index(coords, img_size)
    img = np.bincount(abs_coords, weights=ps, minlength=img_size[0] * img_size[1])
    img = img.reshape(img_size)
    return img[0:sensor_size[0], 0:sensor_size[1]]


def arr_to_red_blue_image(arr, normalize=False, fixed_vmax=3.0, binary_color=False):
    """
    Converts (H, W) array to (H, W, 3) RGB uint8 image.
    Positive values -> Red, Negative values -> Blue

    Args:
        arr: 2D numpy array
        normalize: If True, scales min/max to 0-255
        fixed_vmax: The value that maps to 255 intensity
        binary_color: If True, ignores magnitude (solid colors)
    """
    H, W = arr.shape
    img_rgb = np.zeros((H, W, 3), dtype=np.float32)

    if binary_color:
        img_rgb[arr > 0, 0] = 1.0  # Red for positive
        img_rgb[arr < 0, 2] = 1.0  # Blue for negative
    else:
        if normalize:
            v_abs_max = np.abs(arr).max() + 1e-6
        else:
            v_abs_max = fixed_vmax

        v_scaled = np.clip(arr / v_abs_max, -1.0, 1.0)

        mask_pos = v_scaled > 0
        img_rgb[mask_pos, 0] = v_scaled[mask_pos]

        mask_neg = v_scaled < 0
        img_rgb[mask_neg, 2] = -v_scaled[mask_neg]

    return (img_rgb * 255).astype(np.uint8)


def h5_to_event_frames(h5_path, num_frames=100, fps=30):
    """
    Convert H5 events to a list of RGB frames for video generation.

    @param h5_path Path to H5 file
    @param num_frames Number of frames to generate (controls time binning)
    @param fps Target FPS for the output video (passed to VideoWriter)
    @returns List of (H, W, 3) uint8 frames, sensor_size tuple
    """
    events = read_h5_events_dict(h5_path)
    xs, ys, ts, ps = events['xs'], events['ys'], events['ts'], events['ps']
    height, width = events['height'], events['width']
    sensor_size = (height, width)

    # Normalize timestamps
    t0, t1 = ts[0], ts[-1]
    duration = t1 - t0

    # Calculate time per frame
    dt = duration / num_frames

    frames = []
    for i in range(num_frames):
        t_start = t0 + i * dt
        t_end = t0 + (i + 1) * dt

        # Find events in this time window
        mask = (ts >= t_start) & (ts < t_end)

        if np.sum(mask) > 0:
            frame_xs = xs[mask]
            frame_ys = ys[mask]
            frame_ps = ps[mask]

            # Accumulate events to image
            event_img = events_to_image(frame_xs, frame_ys, frame_ps, sensor_size)

            # Convert to RGB
            rgb_frame = arr_to_red_blue_image(event_img, normalize=False, fixed_vmax=5.0)
        else:
            # Empty frame (black)
            rgb_frame = np.zeros((height, width, 3), dtype=np.uint8)

        frames.append(rgb_frame)

    return frames, sensor_size


def get_h5_info(h5_path):
    """
    Get basic info about an H5 event file.
    @returns dict with num_events, duration, height, width
    """
    try:
        events = read_h5_events_dict(h5_path)
        ts = events['ts']
        return {
            'num_events': len(events['xs']),
            'duration': float(ts[-1] - ts[0]),
            'height': events['height'],
            'width': events['width']
        }
    except Exception as e:
        return {'error': str(e)}
