#!/usr/bin/env python3

import os
import gi
import pyds
import sys
import ipdb
import time
import signal
import platform
import threading
import numpy as np
import multiprocessing as mp
from rich.console import Console
from datetime import datetime, timezone
import json
import atexit
import cv2

import RPi.GPIO as GPIO

#GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(32, GPIO.OUT)
my_pwm = GPIO.PWM(32, 100)
my_pwm.start(0)

GPIO.setup(33, GPIO.OUT)
my_pwm_2 = GPIO.PWM(33, 100)
my_pwm_2.start(0)

atexit.register(GPIO.cleanup)

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstRtspServer

from norfair.tracker import Tracker, Detection

from .config import config, print_config_overrides
from .prints import print_inference as print
from .common import (
    CODEC_MP4,
    CODEC_H264,
    CODEC_H265,
    USBCAM_PROTOCOL,
    RASPICAM_PROTOCOL,
    CONFIG_FILE,
)
from .utils import glib_cb_restart, load_udp_ports_filesaving

LABEL_DEFECTIVE = "Defective"
LABEL_NON_DEFECTIVE = "Non-Defective"
LABEL_GRASS = "grass"
FRAMES_LOG_INTERVAL = int(config["maskcam"]["inference-log-interval"])
SMALL_GRASS_DETECTOR = int(config["grass-detection"]["small-grass-detection"])

# Global vars
frame_number = 0
start_time = None
end_time = None
console = Console()
e_interrupt = None

class RailTrackProcessor:
    def __init__(
        self, th_detection=0, th_vote=0, min_track_size=0, tracker_period=1,
        disable_tracker=False, grass_detector=1, small_grass_detector = 0,
        enable_light = 1, grass_frame_threshold=100
    ):
        self.track_votes = {}
        self.current_tracks = set()
        self.track_detection_times = {}
        self.reported_defective_tracks = set() # new set to track already tracked ids

        # New attributes for grass presence monitoring
        self.grass_consecutive_frames = 0
        self.grass_detected_previously = False
        self.grass_frame_threshold = grass_frame_threshold
        # To store if grass was detected in the current frame by OpenCV
        self.grass_detected_in_current_frame = False
        self.grass_detection = grass_detector
        self.small_grass_detection_enabled = small_grass_detector
        self.enable_light = enable_light
        # New list to store grass detection times

        self.th_detection = th_detection
        self.th_vote = th_vote
        self.tracker_period = tracker_period
        self.min_track_size = min_track_size
        self.disable_detection_validation = False
        self.min_votes = 1
        self.max_votes = 50
        self.color_defective = (1.0, 0.0, 0.0)  # Red
        self.color_grass = (0.0, 1.0, 0.0) # Green
        self.color_non_defective = (0.0, 0.0, 1.0) # Blue
        self.color_unknown = (1.0, 1.0, 0.0)  # yellow
        self.draw_raw_detections = disable_tracker
        self.draw_tracked_objects = not disable_tracker
        self.stats_lock = threading.Lock()

        # Norfair Tracker
        if disable_tracker:
            self.tracker = None
        else:
            self.tracker = Tracker(
                distance_function=self.keypoints_distance,
                detection_threshold=self.th_detection,
                distance_threshold=1,
                point_transience=8,
                hit_inertia_min=15,
                hit_inertia_max=45,
            )

    def keypoints_distance(self, detected_pose, tracked_pose):
        detected_points = detected_pose.points
        estimated_pose = tracked_pose.estimate
        min_box_size = min(
            max(
                detected_points[1][0] - detected_points[0][0],  # x2 - x1
                detected_points[1][1] - detected_points[0][1],  # y2 - y1
                1,
            ),
            max(
                estimated_pose[1][0] - estimated_pose[0][0],  # x2 - x1
                estimated_pose[1][1] - estimated_pose[0][1],  # y2 - y1
                1,
            ),
        )
        mean_distance_normalized = (
            np.mean(np.linalg.norm(detected_points - estimated_pose, axis=1)) / min_box_size
        )
        return mean_distance_normalized

    def validate_detection(self, box_points, score, label):
        if self.disable_detection_validation:
            return True
        box_width = box_points[1][0] - box_points[0][0]
        box_height = box_points[1][1] - box_points[0][1]
        return min(box_width, box_height) >= self.min_track_size and score >= self.th_detection

    def add_detection(self, track_id, label, score):
        # This function is called from cb_buffer_probe everytime it detects an object
        with self.stats_lock:
            self.current_tracks.add(track_id)
            # No voting logic - just track the detection
            if track_id not in self.track_votes:
                self.track_votes[track_id] = 0
            # previous_votes = self.track_votes[track_id]
            # if score > self.th_vote:
            #     if label == LABEL_NON_DEFECTIVE:
            #         self.track_votes[track_id] += 1
            #         print(f"Track {track_id}: +1 vote for Non-defective (score: {score:.3f})")
            #     elif label == LABEL_DEFECTIVE:
            #         self.track_votes[track_id] -= 1
            #         print(f"Track {track_id}: -1 vote for Defective (score: {score:.3f})")
            #         # captures the moment the track is confidently classified as defective                
            #         if previous_votes > -self.min_votes and self.track_votes[track_id] <= -self.min_votes:
            #             if track_id not in self.track_detection_times:
            #                 self.track_detection_times[track_id] = datetime.now()
            #     else:
            #         print(f"Track {track_id}: Unknown label '{label}' with score {score:.3f}")
            #     # max_votes limit
            #     self.track_votes[track_id] = np.clip(
            #         self.track_votes[track_id], -self.max_votes, self.max_votes
            #     )
            # else:
            #     print(f"Track {track_id}: Score {score:.3f} below threshold {self.th_vote}, no vote")

    def get_track_label(self, track_id):
        # track_votes = self.track_votes[track_id]
        # if abs(track_votes) >= self.min_votes:
        #     color = self.color_non_defective if track_votes > 0 else self.color_defective
        #     label = "Non-Defective" if track_votes > 0 else "Defective"  # Changed to match model output
        # else:
        #     color = self.color_unknown
        #     if SMALL_GRASS_DETECTOR:
        #         label = "Grass"
        #     else:
        #         label = "Not visible"
        # return f"{track_id}|{label}({abs(track_votes)})", color
        
        # Completely remove all labels and colors - just show track ID
        # color = self.color_unknown  # Use neutral color for all tracks
        # return f"{track_id}", color
        return f"{track_id}", None
    



    def get_instant_statistics(self, refresh=True):
        """
        Get statistics only including tracks that appeared on camera since last refresh
        Refresh is always TRUE
        """
        instant_stats = self.get_statistics(filter_ids=self.current_tracks) # passing old current_tracks
        if refresh:
            with self.stats_lock:
                # if refreshed new current_tracks is created
                self.current_tracks = set()
        return instant_stats

    def get_statistics(self, filter_ids=None):
        with self.stats_lock:
            if filter_ids is not None:
                filtered_tracks = {
                    id: votes for id, votes in self.track_votes.items() if id in filter_ids
                }
            else:
                filtered_tracks = self.track_votes
            
            defective_tracks_info = []  # Store info about defective tracks
            for track_id in filtered_tracks:
                track_votes = filtered_tracks[track_id]
                if track_votes <= -self.min_votes and track_id in self.track_detection_times:
                    defective_tracks_info.append({
                        'track_id': track_id,
                        'detection_time': self.track_detection_times[track_id].isoformat(),
                        'confidence': abs(track_votes) / self.max_votes
                    })
        return defective_tracks_info


def cb_add_statistics(cb_args): # this function runs independently on a timer -5 seconds
    stats_period, stats_queue, track_processor = cb_args

    defective_tracks_info = track_processor.get_instant_statistics(
        refresh=True
    )

    print(f"No.of Defective tracks detected: {len(defective_tracks_info)}")  # Debug print
    
    newly_reported_defects = []
    with track_processor.stats_lock:
        for defect_info in defective_tracks_info:
            track_id = defect_info['track_id']
            if track_id not in track_processor.reported_defective_tracks:
                newly_reported_defects.append(defect_info)
                track_processor.reported_defective_tracks.add(track_id)

    # if [] -> dont add to stats_queue
    if newly_reported_defects:
        # It's better to put a list of dictionaries, not a list containing a list of dictionaries
        stats_queue.put_nowait(newly_reported_defects)    

    # Next report timeout
    GLib.timeout_add_seconds(stats_period, cb_add_statistics, cb_args)


def sigint_handler(sig, frame):
    # This function is not used if e_external_interrupt is provided
    print("[red]Ctrl+C pressed. Collecting statistics before exit...[/red]")
    if stats_queue is not None:
        print(f"Current queue size: {stats_queue.qsize()}")
        # Give a small delay to ensure all pending statistics are collected
        time.sleep(1)
        print(f"Queue size after delay: {stats_queue.qsize()}")
    e_interrupt.set()


def is_aarch64():
    return platform.uname()[4] == "aarch64"


def draw_detection(display_meta, n_draw, box_points, detection_label, color):
    # print(f"Drawing {n_draw} | {detection_label}")
    # print(box_points)
    rect = display_meta.rect_params[n_draw]

    ((x1, y1), (x2, y2)) = box_points
    rect.left = x1
    rect.top = y1
    rect.width = x2 - x1
    rect.height = y2 - y1
    # print(f"{x1} {y1}, {x2} {y2}")
    
    # Only set color if provided (not None)
    if color is not None:
        rect.border_color.set(*color, 1.0)
        rect.border_width = 2
    else:
        # No border if no color
        rect.border_width = 0
    
    label = display_meta.text_params[n_draw]
    label.x_offset = x1
    label.y_offset = y2
    label.font_params.font_name = "Verdana"
    label.font_params.font_size = 9
    label.font_params.font_color.set(0, 0, 0, 1.0)  # Black
    # label.display_text = f"{person.id} | {detection_p:.2f}"
    label.display_text = detection_label
    
    # Only set background color if color is provided
    if color is not None:
        label.set_bg_clr = True
        label.text_bg_clr.set(*color, 0.5)
    else:
        label.set_bg_clr = False

    display_meta.num_rects = n_draw + 1
    display_meta.num_labels = n_draw + 1


def cb_buffer_probe(pad, info, cb_args):
    global frame_number
    global start_time

    track_processor, e_ready, grass_stats_queue_local = cb_args # Unpack grass_stats_queue
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer", error=True)
        return

    # Set e_ready event to notify the pipeline is working (e.g: for orchestrator)
    if e_ready is not None and not e_ready.is_set():
        print("Inference pipeline setting [green]e_ready[/green]")
        e_ready.set()

    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))

    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting is done by pyds.glist_get_nvds_frame_meta()
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            # frame_meta = pyds.glist_get_nvds_frame_meta(l_frame.data)
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        frame_number = frame_meta.frame_num
        # num_detections = frame_meta.num_obj_meta
        l_obj = frame_meta.obj_meta_list
        detections = []
        obj_meta_list = []
        while l_obj is not None:
            try:
                # Casting l_obj.data to pyds.NvDsObjectMeta
                # obj_meta=pyds.glist_get_nvds_object_meta(l_obj.data)
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break
            obj_meta_list.append(obj_meta)
            obj_meta.rect_params.border_color.set(0.0, 0.0, 1.0, 0.0)
            box = obj_meta.rect_params
            #print(f"Detection: {obj_meta.obj_label} | Confidence: {obj_meta.confidence}")  # Debug print

            box_points = (
                (box.left, box.top),
                (box.left + box.width, box.top + box.height),
            )
            box_p = obj_meta.confidence
            box_label = obj_meta.obj_label
            if track_processor.validate_detection(box_points, box_p, box_label):
                det_data = {"label": box_label, "p": box_p}
                detections.append(
                    Detection(
                        np.array(box_points),
                        data=det_data,
                    )
                )
            try:
                l_obj = l_obj.next
            except StopIteration:
                break


        # Remove all object meta to avoid drawing. Do this outside while since we're modifying list
        for obj_meta in obj_meta_list:
            # Remove this to avoid drawing label texts
            pyds.nvds_remove_obj_meta_from_frame(frame_meta, obj_meta)
        obj_meta_list = None

        # ------------------ Light Intensity Processing ------------------
        if not track_processor.enable_light:
            pass
        else:
            n_frame = pyds.get_nvds_buf_surface(hash(gst_buffer), frame_meta.batch_id)
            
            # Convert to BGR for OpenCV processing
            frame = np.array(n_frame, copy=True, order='C')

            # Convert to grayscale
            gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Calculate the mean pixel value
            mean_value = np.mean(gray_frame)
            x = max(0, min(100, (mean_value / 255) * 100))
            x = 100 - x
            if x < 60:
                x = 0

            # x=0 -> no light
            # x=100 -> max light
            my_pwm.ChangeDutyCycle(x)
            my_pwm_2.ChangeDutyCycle(x)
        # ----------------------------------------------------------------
        # ------------------ Grass Detection using OpenCV ------------------

        # Reset the flag at the beginning of each frame's grass detection phase
        track_processor.grass_detected_in_current_frame = False

        # Check if grass detection is enabled
        if not track_processor.grass_detection:
            pass
        else:
            # Convert BGR to HSV
            hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            # Define a broad range for green color in HSV
            # These values might need tuning based on your specific images and lighting
            # Hue: 0-179 (OpenCV scale), Saturation: 0-255, Value: 0-255
            lower_green = np.array([35, 40, 40])  # ADJUST THESE VALUES
            upper_green = np.array([85, 255, 255]) # ADJUST THESE VALUES

            # Create a mask for green color
            green_mask = cv2.inRange(hsv_frame, lower_green, upper_green)

            # Apply morphological operations to clean up the mask
            kernel = np.ones((5,5),np.uint8)
            green_mask = cv2.erode(green_mask, kernel, iterations = 1) # Erosion removes small specks
            green_mask = cv2.dilate(green_mask, kernel, iterations = 2) # dilation helps connect fragmented regions and fill small holes

            # Find contours in the green mask
            contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Get frame dimensions to calculate total frame area for percentage calculation
            frame_height, frame_width, _ = frame.shape
            total_frame_pixels = frame_height * frame_width

            grass_detections_opencv = []
            total_grass_area_current_frame = 0
            TOTAL_GRASS_AREA_THRESHOLD = 0.30 * total_frame_pixels

            # if grasses of small shape with bbox is needed
            if track_processor.small_grass_detection_enabled:
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 1000:
                        total_grass_area_current_frame += area

                        x, y, w, h = cv2.boundingRect(contour)
                        box_points = ((x, y), (x + w, y + h))
                        confidence = 1.0
                        grass_detections_opencv.append(
                            Detection(
                                np.array(box_points),
                                data={"label": "grass", "p": confidence},
                            )
                        )
                #Append OpenCV detections to the main detections list
                detections.extend(grass_detections_opencv)

            else:
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 1000:
                        total_grass_area_current_frame += area
                    # Now, check the total aggregated grass area for the current frame
                    if total_grass_area_current_frame > TOTAL_GRASS_AREA_THRESHOLD:
                        track_processor.grass_detected_in_current_frame = True # Set flag
                        box_points = ((200, 150), (400, 350)) #large box on center
                        confidence = 1.0
                        #directly add to detections
                        detections.append(
                            Detection(
                                np.array(box_points),
                                data={"label": "grass", "p": confidence},
                            )
                        )
                
            # ------------------------------------------------------------------------------
            # After OpenCV grass detection, update consecutive frame count
            if track_processor.grass_detected_in_current_frame:
                track_processor.grass_consecutive_frames += 1
            else:
                if track_processor.grass_detected_previously:
                    track_processor.grass_consecutive_frames -= 1

            if track_processor.grass_consecutive_frames >= track_processor.grass_frame_threshold:
                grass_founded_time = datetime.now()
                print(f"Grass Detected! at {grass_founded_time}")
                track_processor.grass_detected_previously = True
                track_processor.grass_consecutive_frames = track_processor.grass_frame_threshold # cap at threshold
                
                grass_event_data = {
                    "type": "grass_detected",
                    "time": grass_founded_time.isoformat()
                }

                if grass_stats_queue_local:
                    try:
                        grass_stats_queue_local.put_nowait(grass_event_data)
                    except Exception as e:
                        print(f"Error putting grass event to queue: {e}", error=True)
            
            # Check if grass is no longer detected (after being previously detected)
                elif track_processor.grass_consecutive_frames <= -(track_processor.grass_frame_threshold) and track_processor.grass_detected_previously:
                 # Condition to reset: if count drops significantly below threshold or to 0 after being positive
                    grass_missed_time = datetime.now()
                    print(f"Grass presence dropped below threshold at {grass_missed_time}")
                    track_processor.grass_detected_previously = False
                    # track_processor.grass_consecutive_frames = 0 # Reset counter

                    grass_event_data = {    
                        "type": "grass_stopped",
                        "time": grass_missed_time.isoformat()
                    }
                    if grass_stats_queue_local:
                        try:
                            grass_stats_queue_local.put_nowait(grass_event_data)
                        except Exception as e:
                            print(f"Error putting grass event to queue: {e}", error=True)

        # Each meta object carries max 16 rects/labels/etc.
        max_drawings_per_meta = 16  # This is hardcoded, not documented

        #checks if tracker is enabled
        if track_processor.tracker is not None:
            # Track, count and draw tracked objects
            tracked_objects = track_processor.tracker.update(
                detections, period=track_processor.tracker_period
            )
            # Filter out objects with no live points (don't draw)
            drawn_objects = [obj for obj in tracked_objects if obj.live_points.any()]

            if track_processor.draw_tracked_objects:
                for n_object, obj in enumerate(drawn_objects):
                    points = obj.estimate
                    box_points = points.clip(0).astype(int)

                    # Update track votes
                    track_processor.add_detection(
                        obj.id,
                        obj.last_detection.data["label"],
                        obj.last_detection.data["p"],
                    )
                    label, color = track_processor.get_track_label(obj.id)

                    # Index of this object's drawing in the current meta
                    n_draw = n_object % max_drawings_per_meta

                    if n_draw == 0:  # Initialize meta
                        # Acquiring a display meta object. The memory ownership remains in
                        # the C code so downstream plugins can still access it. Otherwise
                        # the garbage collector will claim it when this probe function exits.
                        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
                        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

                    draw_detection(display_meta, n_draw, box_points, label, color)

        # Raw detections
        # Below lines WON'T RUN if tracker is enabled
        if track_processor.draw_raw_detections:
            for n_detection, detection in enumerate(detections):
                points = detection.points
                box_points = points.clip(0).astype(int)
                label = detection.data["label"]
                if label == "grass":
                    color = track_processor.color_grass
                    color = (0.0, 1.0, 0.0, 0.0) # Green for grass
                if label == LABEL_NON_DEFECTIVE:
                    color = track_processor.color_non_defective
                elif label == LABEL_DEFECTIVE:
                    color = track_processor.color_defective
                else:
                    color = track_processor.color_unknown
                label = f"{label} | {detection.data['p']:.2f}"
                n_draw = n_detection % max_drawings_per_meta

                if n_draw == 0:  # Initialize meta
                    # Acquiring a display meta object. The memory ownership remains in
                    # the C code so downstream plugins can still access it. Otherwise
                    # the garbage collector will claim it when this probe function exits.
                    display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
                    pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
                draw_detection(display_meta, n_draw, box_points, label, color)

            # Using pyds.get_string() to get display_text as string
            # print(pyds.get_string(py_nvosd_text_params.display_text))
            # print(".", end="", flush=True)
        # print("")



        if not frame_number % FRAMES_LOG_INTERVAL:
            print(f"Processed {frame_number} frames...")

        try:
            l_frame = l_frame.next
        except StopIteration:
            break
    # Start timer at the end of first frame processing
    if start_time is None:
        start_time = time.time()
    return Gst.PadProbeReturn.OK


def cb_newpad(decodebin, decoder_src_pad, data):
    print("In cb_newpad\n")
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    source_bin = data
    features = caps.get_features(0)

    # Need to check if the pad created by the decodebin is for video and not
    # audio.
    print("gstname=", gstname)
    if gstname.find("video") != -1:
        # Link the decodebin pad only if decodebin has picked nvidia
        # decoder plugin nvdec_*. We do this by checking if the pad caps contain
        # NVMM memory features.
        if features.contains("memory:NVMM"):
            # Get the source bin ghost pad
            bin_ghost_pad = source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                print("Failed to link decoder src pad to source bin ghost pad", error=True)
        else:
            print("Decodebin did not pick nvidia decoder plugin", error=True)


def decodebin_child_added(child_proxy, Object, name, user_data):
    print(f"Decodebin child added: {name}")
    if name.find("decodebin") != -1:
        Object.connect("child-added", decodebin_child_added, user_data)
    if is_aarch64() and name.find("nvv4l2decoder") != -1:
        Object.set_property("bufapi-version", True)


def create_source_bin(index, uri):
    print("Creating source bin")

    # Create a source GstBin to abstract this bin's content from the rest of the
    # pipeline
    bin_name = "source-bin-%02d" % index
    print(bin_name)
    nbin = Gst.Bin.new(bin_name)
    if not nbin:
        print("Unable to create source bin", error=True)

    # Source element for reading from the uri.
    # We will use decodebin and let it figure out the container format of the
    # stream and the codec and plug the appropriate demux and decode plugins.
    uri_decode_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not uri_decode_bin:
        print("Unable to create uri decode bin", error=True)
    # We set the input uri to the source element
    uri_decode_bin.set_property("uri", uri)
    # Connect to the "pad-added" signal of the decodebin which generates a
    # callback once a new pad for raw data has beed created by the decodebin
    uri_decode_bin.connect("pad-added", cb_newpad, nbin)
    uri_decode_bin.connect("child-added", decodebin_child_added, nbin)

    # We need to create a ghost pad for the source bin which will act as a proxy
    # for the video decoder src pad. The ghost pad will not have a target right
    # now. Once the decode bin creates the video decoder and generates the
    # cb_newpad callback, we will set the ghost pad target to the video decoder
    # src pad.
    Gst.Bin.add(nbin, uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC))
    if not bin_pad:
        print("Failed to add ghost pad in source bin", error=True)
        return None
    return nbin


def make_elm_or_print_err(factoryname, name, printedname):
    """Creates an element with Gst Element Factory make.
    Return the element  if successfully created, otherwise print
    to stderr and return None.
    """
    print("Creating", printedname)
    elm = Gst.ElementFactory.make(factoryname, name)
    if not elm:
        print("Unable to create ", printedname, error=True)
        show_troubleshooting()
    return elm


def show_troubleshooting():
    # On Jetson, there is a problem with the encoder failing to initialize
    # due to limitation on TLS usage. To work around this, preload libgomp.
    # Add a reminder here in case the user forgets.
    print(
        """
    [yellow]TROUBLESHOOTING HELP[/yellow]

    [yellow]If the error is like: v4l-camera-source / reason not-negotiated[/yellow]
    [green]Solution:[/green] configure camera capabilities
    Run the script under utils/gst_capabilities.sh and find the lines with type
    video/x-raw ...
    Find a suitable framerate=X/1 (with X being an integer like 24, 15, etc.)
    Then edit config_maskcam.txt and change the line:
    camera-framerate=X
    Or configure using --env MASKCAM_CAMERA_FRAMERATE=X (see README)

    [yellow]If the error is like:
    /usr/lib/aarch64-linux-gnu/libgomp.so.1: cannot allocate memory in static TLS block[/yellow]
    [green]Solution:[/green] preload the offending library
    export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1

    [yellow]END HELP[/yellow]
    """
    )


def main(
    config: dict,
    input_filename: str,
    output_filename: str = None,
    e_external_interrupt: mp.Event = None,
    stats_queue: mp.Queue = None,
    grass_stats_queue: mp.Queue = None, # Add grass_stats_queue parameter
    e_ready: mp.Event = None,
):
    global frame_number
    global start_time
    global end_time
    global e_interrupt

    # Load all udp ports to output video
    udp_ports = {int(config["maskcam"]["udp-port-streaming"])}
    load_udp_ports_filesaving(config, udp_ports)

    codec = config["maskcam"]["codec"]
    stats_period = int(config["maskcam"]["statistics-period"]) #15 sec


    # Original: 1920x1080, bdti_resized: 1024x576, yolo-input: 1024x608
    # Nice for h264@1024x576: 4000000
    output_width = int(config["maskcam"]["output-video-width"])
    output_height = int(config["maskcam"]["output-video-height"])
    output_bitrate = 1000000 # 1Mbps

    # Two types of camera supported: USB or Raspi
    usbcam_input = USBCAM_PROTOCOL in input_filename
    raspicam_input = RASPICAM_PROTOCOL in input_filename
    camera_input = usbcam_input or raspicam_input
    if camera_input:
        camera_framerate = int(config["maskcam"]["camera-framerate"])
        camera_flip_method = int(config["maskcam"]["camera-flip-method"])

    # Set nvinfer.interval (number of frames to skip inference and use tracker instead)
    if camera_input and int(config["maskcam"]["inference-interval-auto"]):
        max_fps = int(config["maskcam"]["inference-max-fps"])
        skip_inference = camera_framerate // max_fps
        print(f"Auto calculated frames to skip inference: {skip_inference}")
    else:
        skip_inference = int(config["property"]["interval"])
        print(f"Configured frames to skip inference: {skip_inference}")

    # RailTrack initialization
    track_tracker_period = skip_inference + 1  # tracker_period=skipped + inference frame(1)
    track_detection_threshold = float(config["track-processor"]["detection-threshold"])
    track_voting_threshold = float(config["track-processor"]["voting-threshold"])
    track_min_track_size = int(config["track-processor"]["min-track-size"])
    track_disable_tracker = int(config["track-processor"]["disable-tracker"])
    small_grass_detector = int(config["grass-detection"]["small-grass-detection"])
    grass_detector = int(config["grass-detection"]["grass-detection"])
    grass_frame_threshold = int(config["grass-detection"]["frame-threshold"])
    enable_light = int(config["light"]["light-processing"])
    track_processor = RailTrackProcessor(
        th_detection=track_detection_threshold,
        th_vote=track_voting_threshold,
        min_track_size=track_min_track_size,
        tracker_period=track_tracker_period,
        disable_tracker=track_disable_tracker,
        grass_detector=grass_detector,
        small_grass_detector=small_grass_detector,
        enable_light = enable_light,
        grass_frame_threshold = grass_frame_threshold
    )

    # Standard GStreamer initialization
    Gst.init(None)

    # Create gstreamer elements
    # Create Pipeline element that will form a connection of other elements
    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()

    if not pipeline:
        print("Unable to create Pipeline", error=True)

    if camera_input:
        if usbcam_input:
            input_device = input_filename[len(USBCAM_PROTOCOL) :]
            source = make_elm_or_print_err("v4l2src", "v4l2-camera-source", "Camera input")
            source.set_property("device", input_device)
            nvvidconvsrc = make_elm_or_print_err(
                "nvvideoconvert", "convertor_src2", "Convertor src 2"
            )

            # Input camera configuration
            # Use ./gst_capabilities.sh to get the list of available capabilities from /dev/video0
            camera_capabilities = f"video/x-raw, framerate={camera_framerate}/1"
        elif raspicam_input:
            input_device = input_filename[len(RASPICAM_PROTOCOL) :]
            source = make_elm_or_print_err(
                "nvarguscamerasrc", "nv-argus-camera-source", "RaspiCam input"
            )
            source.set_property("sensor-id", int(input_device))
            source.set_property("bufapi-version", 1)

            # Special camera_capabilities for raspicam
            camera_capabilities = f"video/x-raw(memory:NVMM),framerate={camera_framerate}/1"
            nvvidconvsrc = make_elm_or_print_err("nvvidconv", "convertor_flip", "Convertor flip")
            nvvidconvsrc.set_property("flip-method", camera_flip_method)

        # Misterious converting sequence from deepstream_test_1_usb.py
        caps_camera = make_elm_or_print_err("capsfilter", "camera_src_caps", "Camera caps filter")
        caps_camera.set_property(
            "caps",
            Gst.Caps.from_string(camera_capabilities),
        )
        vidconvsrc = make_elm_or_print_err("videoconvert", "convertor_src1", "Convertor src 1")
        caps_vidconvsrc = make_elm_or_print_err(
            "capsfilter", "nvmm_caps", "NVMM caps for input stream"
        )
        caps_vidconvsrc.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM)"))
    else:
        source_bin = create_source_bin(0, input_filename)

    # Create nvstreammux instance to form batches from one or more sources.
    streammux = make_elm_or_print_err("nvstreammux", "Stream-muxer", "NvStreamMux")
    streammux.set_property("width", output_width)
    streammux.set_property("height", output_height)
    streammux.set_property("enable-padding", True)  # Keeps aspect ratio, but adds black margin
    streammux.set_property("batch-size", 1)
    streammux.set_property("batched-push-timeout", 4000000)

    # Adding this element after muxer will cause detections to get delayed
    # videorate = make_elm_or_print_err("videorate", "Vide-rate", "Video Rate")

    # Inference element: object detection using TRT engine
    pgie = make_elm_or_print_err("nvinfer", "primary-inference", "pgie")
    pgie.set_property("config-file-path", CONFIG_FILE)
    pgie.set_property("interval", skip_inference)

    # Use convertor to convert from NV12 to RGBA as required by nvosd
    convert_pre_osd = make_elm_or_print_err(
        "nvvideoconvert", "convert_pre_osd", "Converter NV12->RGBA"
    )

    # OSD: to draw on the RGBA buffer
    nvosd = make_elm_or_print_err("nvdsosd", "onscreendisplay", "OSD (nvosd)")
    nvosd.set_property("process-mode", 2)  # 0: CPU Mode, 1: GPU (only dGPU), 2: VIC (Jetson only)
    # nvosd.set_property("display-bbox", False)  # Bug: Removes all squares
    nvosd.set_property("display-clock", False)
    nvosd.set_property("display-text", True)  # Needed for any text

    # Finally encode and save the osd output
    queue = make_elm_or_print_err("queue", "queue", "Queue")
    convert_post_osd = make_elm_or_print_err(
        "nvvideoconvert", "convert_post_osd", "Converter RGBA->NV12"
    )

    # Video capabilities: check format and GPU/CPU location
    capsfilter = make_elm_or_print_err("capsfilter", "capsfilter", "capsfilter")
    if codec == CODEC_MP4:  # Not hw accelerated
        caps = Gst.Caps.from_string("video/x-raw, format=I420")
    else:  # hw accelerated
        caps = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420")
    capsfilter.set_property("caps", caps)

    # Encoder: H265 has more efficient compression
    if codec == CODEC_MP4:
        print("Creating MPEG-4 stream")
        encoder = make_elm_or_print_err("avenc_mpeg4", "encoder", "Encoder")
        codeparser = make_elm_or_print_err("mpeg4videoparse", "mpeg4-parser", "Code Parser")
        rtppay = make_elm_or_print_err("rtpmp4vpay", "rtppay", "RTP MPEG-44 Payload")
    elif codec == CODEC_H264:
        print("Creating H264 stream")
        encoder = make_elm_or_print_err("nvv4l2h264enc", "encoder", "Encoder")
        encoder.set_property("preset-level", 1)
        encoder.set_property("bufapi-version", 1)
        codeparser = make_elm_or_print_err("h264parse", "h264-parser", "Code Parser")
        rtppay = make_elm_or_print_err("rtph264pay", "rtppay", "RTP H264 Payload")
    else:  # Default: H265 (recommended)
        print("Creating H265 stream")
        encoder = make_elm_or_print_err("nvv4l2h265enc", "encoder", "Encoder")
        encoder.set_property("preset-level", 1)
        encoder.set_property("bufapi-version", 1)
        codeparser = make_elm_or_print_err("h265parse", "h265-parser", "Code Parser")
        rtppay = make_elm_or_print_err("rtph265pay", "rtppay", "RTP H265 Payload")

    encoder.set_property("insert-sps-pps", 1)
    encoder.set_property("bitrate", output_bitrate)

    splitter_file_udp = make_elm_or_print_err("tee", "tee_file_udp", "Splitter file/UDP")

    # UDP streaming
    queue_udp = make_elm_or_print_err("queue", "queue_udp", "UDP queue")
    multiudpsink = make_elm_or_print_err("multiudpsink", "multi udpsink", "Multi UDP Sink")
    # udpsink.set_property("host", "127.0.0.1")
    # udpsink.set_property("port", udp_port)

    # Comma separated list of clients, don't add spaces :S
    client_list = [f"127.0.0.1:{udp_port}" for udp_port in udp_ports]
    multiudpsink.set_property("clients", ",".join(client_list))

    multiudpsink.set_property("async", False)
    multiudpsink.set_property("sync", True)

    if output_filename is not None:
        queue_file = make_elm_or_print_err("queue", "queue_file", "File save queue")
        # codeparser already created above depending on codec
        container = make_elm_or_print_err("qtmux", "qtmux", "Container")
        filesink = make_elm_or_print_err("filesink", "filesink", "File Sink")
        filesink.set_property("location", output_filename)
    else:  # Fake sink, no save
        fakesink = make_elm_or_print_err("fakesink", "fakesink", "Fake Sink")

    # Add elements to the pipeline
    if camera_input:
        pipeline.add(source)
        pipeline.add(caps_camera)
        pipeline.add(vidconvsrc)
        pipeline.add(nvvidconvsrc)
        pipeline.add(caps_vidconvsrc)
    else:
        pipeline.add(source_bin)
    pipeline.add(streammux)
    pipeline.add(pgie)

    pipeline.add(convert_pre_osd)
    pipeline.add(nvosd)
    pipeline.add(queue)
    pipeline.add(convert_post_osd)
    pipeline.add(capsfilter)
    pipeline.add(encoder)
    pipeline.add(splitter_file_udp)

    if output_filename is not None:
        pipeline.add(queue_file)
        pipeline.add(codeparser)
        pipeline.add(container)
        pipeline.add(filesink)
    else:
        pipeline.add(fakesink)

    # Output to UDP
    pipeline.add(queue_udp)
    pipeline.add(rtppay)
    pipeline.add(multiudpsink)

    print("Linking elements in the Pipeline \n")

    # Pipeline Links
    if camera_input:
        source.link(caps_camera)
        caps_camera.link(vidconvsrc)
        vidconvsrc.link(nvvidconvsrc)
        nvvidconvsrc.link(caps_vidconvsrc)
        srcpad = caps_vidconvsrc.get_static_pad("src")
    else:
        srcpad = source_bin.get_static_pad("src")
    sinkpad = streammux.get_request_pad("sink_0")
    if not srcpad or not sinkpad:
        print("Unable to get file source or mux sink pads", error=True)
    srcpad.link(sinkpad)
    streammux.link(pgie)
    pgie.link(convert_pre_osd)
    convert_pre_osd.link(nvosd)
    nvosd.link(queue)
    queue.link(convert_post_osd)
    convert_post_osd.link(capsfilter)
    capsfilter.link(encoder)
    encoder.link(splitter_file_udp)

    # Split stream to file and rtsp
    tee_file = splitter_file_udp.get_request_pad("src_%u")
    tee_udp = splitter_file_udp.get_request_pad("src_%u")

    # Output to File or fake sinks
    if output_filename is not None:
        tee_file.link(queue_file.get_static_pad("sink"))
        queue_file.link(codeparser)
        codeparser.link(container)
        container.link(filesink)
    else:
        tee_file.link(fakesink.get_static_pad("sink"))

    # Output to UDP
    tee_udp.link(queue_udp.get_static_pad("sink"))
    queue_udp.link(rtppay)
    rtppay.link(multiudpsink)

    # Lets add probe to get informed of the meta data generated, we add probe to
    # the sink pad of the osd element, since by that time, the buffer would have
    # had got all the metadata.
    osdsinkpad = nvosd.get_static_pad("sink")
    if not osdsinkpad:
        print("Unable to get sink pad of nvosd", error=True)

    cb_args = (track_processor, e_ready, grass_stats_queue) # Pass grass_stats_queue to cb_args
    osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, cb_buffer_probe, cb_args)

    # GLib loop required for RTSP server
    g_loop = GLib.MainLoop()
    g_context = g_loop.get_context()

    # GStreamer message bus
    bus = pipeline.get_bus()

    if e_external_interrupt is None:
        # Use threading instead of mp.Event() for sigint_handler, see:
        # https://bugs.python.org/issue41606
        e_interrupt = threading.Event()
        signal.signal(signal.SIGINT, sigint_handler)
        print("[green bold]Press Ctrl+C to stop pipeline[/green bold]")
    else:
        # If there's an external interrupt, don't capture SIGINT
        e_interrupt = e_external_interrupt

    # start play back and listen to events
    pipeline.set_state(Gst.State.PLAYING)

    # After setting pipeline to PLAYING, stop it even on exceptions
    try:
        time_start_playing = time.time()

        # Timer to add statistics to queue
        if stats_queue is not None:
            cb_args = stats_period, stats_queue, track_processor
            GLib.timeout_add_seconds(stats_period, cb_add_statistics, cb_args)

        # Periodic gloop interrupt (see utils.glib_cb_restart)
        t_check = 100
        GLib.timeout_add(t_check, glib_cb_restart, t_check)

        # Custom event loop
        running = True
        while running:
            g_context.iteration(may_block=True)

            message = bus.pop()
            if message is not None:
                t = message.type

                if t == Gst.MessageType.EOS:
                    print("End-of-stream\n")
                    running = False
                elif t == Gst.MessageType.WARNING:
                    err, debug = message.parse_warning()
                    print(f"{err}: {debug}", warning=True)
                elif t == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    print(f"{err}: {debug}", error=True)
                    show_troubleshooting()
                    running = False
            if e_interrupt.is_set():
                # Send EOS to container to generate a valid mp4 file
                if output_filename is not None:
                    container.send_event(Gst.Event.new_eos())
                    multiudpsink.send_event(Gst.Event.new_eos())
                else:
                    pipeline.send_event(Gst.Event.new_eos())  # fakesink EOS won't work

        end_time = time.time()
        print("Inference main loop ending.")
        pipeline.set_state(Gst.State.NULL)

        # Profiling display
        if start_time is not None and end_time is not None:
            total_time = end_time - start_time
            total_frames = frame_number
            inference_frames = total_frames // (skip_inference + 1)
            print()
            print(f"[bold yellow] ---- Profiling ---- [/bold yellow]")
            print(f"Inference frames: {inference_frames} | Processed frames: {total_frames}")
            print(f"Time from time_start_playing: {end_time - time_start_playing:.2f} seconds")
            print(f"Total time skipping first inference: {total_time:.2f} seconds")
            print(f"Avg. time/frame: {total_time/total_frames:.4f} secs")
            print(f"[bold yellow]FPS: {total_frames/total_time:.1f} frames/second[/bold yellow]\n")
            if skip_inference != 0:
                print(
                    "[red]NOTE: FPS calculated skipping inference every"
                    f" interval={skip_inference} frames[/red]"
                )
        if output_filename is not None:
            print(f"Output file saved: [green bold]{output_filename}[/green bold]")

    except:
        console.print_exception()
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    print_config_overrides()
    # Check input arguments
    output_filename = None
    if len(sys.argv) > 1:
        input_filename = sys.argv[1]
        print(f"Provided input source: {input_filename}")
        if len(sys.argv) > 2:
            output_filename = sys.argv[2]
            print(f"Save output file: [green]{output_filename}[/green]")
    else:
        input_filename = config["maskcam"]["default-input"]
        print(f"Using input from config file: {input_filename}")

    # Initialize stats queue and create statistics directory
    stats_queue = mp.Queue()
    stats_dir = config["maskcam"]["fileserver-hdd-dir"]
    os.makedirs(stats_dir, exist_ok=True)

    sys.exit(
        main(
            config=config,
            input_filename=input_filename,
            output_filename=output_filename,
            stats_queue=stats_queue,
            grass_stats_queue=grass_stats_queue_main, # Pass it here for standalone
        )
    )
