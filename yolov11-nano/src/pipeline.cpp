#include <gst/gst.h>
#include <glib.h>

// Bus callback to handle messages (EOS, errors)
static gboolean bus_call(GstBus *bus, GstMessage *msg, gpointer data) {
    GMainLoop *loop = (GMainLoop *)data;
    switch (GST_MESSAGE_TYPE(msg)) {
        case GST_MESSAGE_EOS:
            g_print("End of stream\n");
            g_main_loop_quit(loop);
            break;
        case GST_MESSAGE_ERROR: {
            gchar *debug;
            GError *error;
            gst_message_parse_error(msg, &error, &debug);
            g_printerr("ERROR from element %s: %s\n", GST_OBJECT_NAME(msg->src), error->message);
            g_printerr("Debugging info: %s\n", debug ? debug : "none");
            g_error_free(error);
            g_free(debug);
            g_main_loop_quit(loop);
            break;
        }
        default:
            break;
    }
    return TRUE;
}

int main(int argc, char *argv[]) {
    GMainLoop *loop;
    GstElement *pipeline, *source, *streammux, *pgie, *osd, *sink;
    GstBus *bus;
    guint bus_watch_id;

    // Initialize GStreamer
    gst_init(&argc, &argv);
    loop = g_main_loop_new(NULL, FALSE);

    // Create pipeline and elements
    pipeline = gst_pipeline_new("deepstream-pipeline");
    source = gst_element_factory_make("nvurisrcbin", "source");
    streammux = gst_element_factory_make("nvstreammux", "streammux");
    pgie = gst_element_factory_make("nvinfer", "primary-inference");
    osd = gst_element_factory_make("nvosd", "osd");
    sink = gst_element_factory_make("nveglglessink", "sink");

    // Check if elements were created successfully
    if (!pipeline || !source || !streammux || !pgie || !osd || !sink) {
        g_printerr("Failed to create one or more elements. Exiting.\n");
        return -1;
    }

    // Set properties for each element
    // Source
    g_object_set(G_OBJECT(source),
                 "uri", "file:///home/lab5/Desktop/Railway_faults.fyp/yolov11-nano/railway_fault640_640.mp4",
                 "gpu-id", 0,
                 NULL);

    // Streammux
    g_object_set(G_OBJECT(streammux),
                 "width", 1280,
                 "height", 720,
                 "batch-size", 1,
                 "batched-push-timeout", 40000,
                 "live-source", 0,
                 "enable-padding", 0,
                 "nvbuf-memory-type", 0,
                 "gpu-id", 0,
                 NULL);

    // Primary GIE (Inference)
    g_object_set(G_OBJECT(pgie),
                 "config-file-path", "config_infer_primary_yoloV11.txt",
                 "unique-id", 1,
                 "gpu-id", 0,
                 "nvbuf-memory-type", 0,
                 NULL);

    // OSD
    g_object_set(G_OBJECT(osd),
                 "gpu-id", 0,
                 "border-width", 5,
                 "text-size", 15,
                 "text-color", "1;1;1;1",
                 "text-bg-color", "0.3;0.3;0.3;1",
                 "font", "Serif",
                 "show-clock", 0,
                 "nvbuf-memory-type", 0,
                 NULL);

    // Sink
    g_object_set(G_OBJECT(sink),
                 "sync", 0,
                 "gpu-id", 0,
                 "nvbuf-memory-type", 0,
                 NULL);

    // Add elements to the pipeline
    gst_bin_add_many(GST_BIN(pipeline), source, streammux, pgie, osd, sink, NULL);

    // Link source to streammux (requires pad linking)
    GstPad *sinkpad = gst_element_get_request_pad(streammux, "sink_0");
    GstPad *srcpad = gst_element_get_static_pad(source, "src");
    if (gst_pad_link(srcpad, sinkpad) != GST_PAD_LINK_OK) {
        g_printerr("Failed to link source to streammux. Exiting.\n");
        return -1;
    }
    gst_object_unref(srcpad);
    gst_object_unref(sinkpad);

    // Link remaining elements
    if (!gst_element_link_many(streammux, pgie, osd, sink, NULL)) {
        g_printerr("Failed to link elements. Exiting.\n");
        return -1;
    }

    // Set up message handling
    bus = gst_element_get_bus(pipeline);
    bus_watch_id = gst_bus_add_watch(bus, bus_call, loop);
    gst_object_unref(bus);

    // Start the pipeline
    gst_element_set_state(pipeline, GST_STATE_PLAYING);
    g_print("Pipeline is running...\n");

    // Run the main loop
    g_main_loop_run(loop);

    // Cleanup
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(pipeline);
    g_source_remove(bus_watch_id);
    g_main_loop_unref(loop);

    return 0;
}
