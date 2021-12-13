#t5 utils
import argparse, os
import kso_utils.db_utils as db_utils
import pandas as pd
import numpy as np
import math
import logging
import pims

from tqdm import tqdm
from PIL import Image
from IPython.display import HTML, display, update_display, clear_output
import ipywidgets as widgets
from ipywidgets import interact, Layout
from kso_utils.zooniverse_utils import auth_session, populate_agg_annotations
import kso_utils.tutorials_utils as t_utils
import kso_utils.server_utils as s_utils
import kso_utils.t12_utils as t12
import kso_utils.koster_utils as k_utils
from ipyfilechooser import FileChooser
from pathlib import Path

from panoptes_client import (
    SubjectSet,
    Subject,
    Project,
    Panoptes,
)

# Logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
out_df = pd.DataFrame()

def choose_species(db_path: str = "koster_lab.db"):
    conn = db_utils.create_connection(db_path)
    species_list = pd.read_sql_query("SELECT label from species", conn)["label"].tolist()
    w = widgets.SelectMultiple(
        options=species_list,
        value=[species_list[0]],
        description='Species',
        disabled=False
    )

    display(w)
    return w

def choose_folder():
    fc = FileChooser('.')
    display(fc)
    return fc

def get_species_ids(species_list: list):
    """
    # Get ids of species of interest
    """
    species_id = pd.read_sql_query(
        f'SELECT id FROM species WHERE label="{args.species}"', conn
    ).values[0][0]
    return get_species_ids
    
def check_frames_uploaded(frames_df: pd.DataFrame, project_name, species_ids, conn):
    # Get info of frames already uploaded
    # Set the columns in the right order
    if len(species_ids) <= 1:
        species_ids = pd.read_sql_query(f"SELECT id as species_id FROM species WHERE label=='{species_ids[0]}'", conn).species_id.values
        uploaded_frames_df = pd.read_sql_query(f"SELECT movie_id, frame_number, frame_exp_sp_id FROM subjects WHERE frame_exp_sp_id=='{species_ids[0]}' AND subject_type='frame'", conn)
    
    else:
        species_ids = pd.read_sql_query(f"SELECT id as species_id FROM species WHERE label IN {tuple(species_ids)}", conn).species_id.values
        uploaded_frames_df = pd.read_sql_query(
        f"SELECT movie_id, frame_number, frame_exp_sp_id FROM subjects WHERE frame_exp_sp_id IN {tuple(species_ids)} AND subject_type='frame'",
    conn,
    )
    
    # Filter out frames that have already been uploaded
    #if len(uploaded_frames_df) > 0:

        # Exclude frames that have already been uploaded
    #    frames_df = frames_df[
    #        ~(frames_df["movie_id"].isin(uploaded_frames_df["movie_id"]))
    #        & ~(frames_df["frame_number"].isin(uploaded_frames_df["frame_number"]))
    #        & ~(
    #            frames_df["species_id"].isin(
    #                uploaded_frames_df["frame_exp_sp_id"]
    #            )
    #        )
    #    ]
    return frames_df

def extract_frames(df, server_dict, project_name, frames_folder):
    """
    Extract frames and save them in chosen folder.
    """
    
    movie_folder = t_utils.get_project_info(project_name, "movie_folder")
    
    # Create the folder to store the frames if not exist
    if not os.path.exists(frames_folder):
        os.mkdir(frames_folder)

    # Get movies filenames from their path
    df["movie_filename"] = df["fpath"].apply(lambda x: os.path.splitext(os.path.basename(x))[0])

    # Set the filename of the frames
    df["frame_path"] = (
        frames_folder
        + df["movie_filename"].astype(str)
        + "_frame_"
        + df["frame_number"].astype(str)
        + "_"
        + df["species_id"].astype(str)
        + ".jpg"
    )

    # Download movies that are not available locally
    if len(df["fpath"].unique()) > 5:
        logging.error(f"You are about to download {len(df['fpath'].unique())} movies to your local machine. We recommend running this notebook on your SNIC server environment directly instead to limit transfer volume.")
    
    else:
        for k in df["fpath"].unique():
            if not os.path.exists(k):
                print(k)
                # Download the movie of interest
                s_utils.download_object_from_snic(
                                server_dict["sftp_client"],
                                remote_fpath=k,#k_utils.reswedify(k),
                                local_fpath=str(Path(".", k_utils.unswedify(os.path.basename(k))))
                )
    
        video_dict = {k: pims.Video(str(Path(".", k_utils.unswedify(os.path.basename(k))))) for k in df["fpath"].unique()}

        # Save the frame as matrix
        df["frames"] = df[["fpath", "frame_number"]].apply(
            lambda x: video_dict[x["fpath"]][int(x["frame_number"])],
            1,
        )

        # Extract and save frames
        for frame, filename in zip(df["frames"], df["frame_path"]):
            Image.fromarray(frame).save(f"{filename}")

        print("Frames extracted successfully")
        return df

def set_zoo_metadata(df, species_list, project_name, db_info_dict):
    
    if not isinstance(df, pd.DataFrame):
        df = df.df
        
    upload_to_zoo = df[["frame_path", "species_id", "movie_id"]]
    
    movies_df = pd.read_csv(db_info_dict["local_movies_csv"])
    
    upload_to_zoo = upload_to_zoo.merge(movies_df, left_on="movie_id",
                                            right_on="movie_id")
    
    created_on = upload_to_zoo["created_on"].unique()[0]
    sitename = upload_to_zoo["siteName"].unique()[0]
 
    return upload_to_zoo, sitename, created_on

def create_frames(sp_frames_df: pd.DataFrame):
    # Create the folder to store the frames if not exist
    if not os.path.exists(args.frames_folder):
        os.mkdir(args.frames_folder)

    # Extract the frames and save them
    sp_frames_df["frame_path"] = extract_frames(sp_frames_df, args.frames_folder)
    sp_frames_df = sp_frames_df.drop_duplicates(subset=['frame_path'])

    # Select koster db metadata associated with each frame
    sp_frames_df["label"] = args.species
    sp_frames_df["subject_type"] = "frame"

    sp_frames_df = sp_frames_df[
        [
            "frame_path",
            "frame_number",
            "fps",
            "movie_id",
            "label",
            "frame_exp_sp_id",
            "subject_type",
        ]
    ]
    return sp_frames_df

def get_frames(species_ids: list, db_path: str, zoo_info_dict: dict, server_dict: dict, project_name: str, n_frames=300):
    
    movie_folder = t_utils.get_project_info(project_name, "movie_folder")
    df = pd.DataFrame()
    
    if movie_folder == "None":
        
        df = FileChooser('.')
            
        # Callback function
        def build_df(chooser):
            frame_files = os.listdir(chooser.selected)
            frame_paths = [chooser.selected+i for i in frame_files]
            try:
                os.symlink(chooser.selected[:-1], 'linked_frames')
            except FileExistsError:
                os.remove('linked_frames')
                os.symlink(chooser.selected[:-1], 'linked_frames')
            chooser.df = pd.DataFrame(frame_paths, columns=["frame_path"])
                
        # Register callback function
        df.register_callback(build_df)
        display(df)
        
    else:
        # Connect to koster_db
        conn = db_utils.create_connection(db_path)
        workflows_out = t12.WidgetMaker(zoo_info_dict["workflows"])
        display(workflows_out)
        agg_params = t12.choose_agg_parameters("clip")

        df = FileChooser('.')
        df.title = '<b>Choose location to store frames</b>'
            
        # Callback function
        def extract_files(chooser):
            clips_df = t12.get_classifications(workflows_out.checks,
                                               zoo_info_dict["workflows"], "clip", zoo_info_dict["classifications"], db_path)
        
            agg_clips_df, raw_clips_df = t12.aggregrate_classifications(clips_df, "clip", project_name, agg_params=agg_params)
            agg_clips_df = agg_clips_df.rename(columns={"frame_exp_sp_id": "species_id"})

            populate_agg_annotations(agg_clips_df, "clip", project_name)
            frame_df = get_species_frames(species_ids, server_dict, conn, project_name, n_frames)
            frame_df = check_frames_uploaded(frame_df, project_name, species_ids, conn)
            chooser.df = extract_frames(frame_df, server_dict, project_name, chooser.selected)
            try:
                os.symlink(chooser.selected[:-1], 'linked_frames')
            except FileExistsError:
                os.remove('linked_frames')
                os.symlink(chooser.selected[:-1], 'linked_frames')
                
        # Register callback function
        df.register_callback(extract_files)
        display(df)
        
    return df

def compare_frames(df):
    
    if not isinstance(df, pd.DataFrame):
        df = df.df

    # Save the paths of the clips
    original_frame_paths = df["frame_path"].unique()
    
    # Add "no movie" option to prevent conflicts
    original_frame_paths = np.append(original_frame_paths,"No frame")
    
    clip_path_widget = widgets.Dropdown(
                    options=tuple(np.sort(original_frame_paths)),
                    description="Select original frame:",
                    ensure_option=True,
                    disabled=False,
                    layout=Layout(width='50%'),
                    style = {'description_width': 'initial'}
                )
    
    main_out = widgets.Output()
    display(clip_path_widget, main_out)
    
    # Display the original and modified clips
    def on_change(change):
        with main_out:
            clear_output()
            if change["new"]=="No frame":
                print("It is OK to modify the frames again")
            else:
                a = view_frames(df, change["new"])
                display(a)
                   
    clip_path_widget.observe(on_change, names='value')
    
# Display the clips using html
def view_frames(df, frame_path):
    
    # Get path of the modified clip selected
    #modified_clip_path = df[df["clip_path"]==movie_path].modif_clip_path.values[0]
    #print(modified_clip_path)
    
    base_dir = "linked_frames"
    file_name = os.path.basename(frame_path)
        
    html_code = f"""
        <html>
        <div style="display: flex; justify-content: space-around">
        <div>
          <img src='{str(Path(base_dir, file_name))}'>
        </img>
        </div>
        <div>
          <img src='{str(Path(base_dir, file_name))}'>
        </img>
        </div>
        </html>"""
   
    return HTML(html_code)

    
def get_species_frames(species_ids: list, server_dict: dict, conn, project_name, n_frames):
    """
    # Function to identify up to n number of frames per classified clip
    # that contains species of interest after the first time seen

    # Find classified clips that contain the species of interest
    """
    server = t_utils.get_project_info(project_name, "server")
    
    if server == "SNIC" and project_name == "Koster_Seafloor_Obs":
        
        movie_folder = t_utils.get_project_info(project_name, "movie_folder")
        
        # Set the columns in the right order
        if len(species_ids) <= 1:
            species_ids = pd.read_sql_query(f"SELECT id as species_id FROM species WHERE label=='{species_ids[0]}'", conn).species_id.values
            frames_df = pd.read_sql_query(
            f"SELECT subject_id, first_seen, species_id FROM agg_annotations_clip WHERE agg_annotations_clip.species_id== '{species_ids[0]}'",
            conn,
        )
        else:
            species_ids = pd.read_sql_query(f"SELECT id as species_id FROM species WHERE label IN {tuple(species_ids)}", conn).species_id.values
            frames_df = pd.read_sql_query(
            f"SELECT subject_id, first_seen, species_id FROM agg_annotations_clip WHERE agg_annotations_clip.species_id IN {tuple(species_ids)}",
            conn,
        )

        subjects_df = pd.read_sql_query(
                    f"SELECT id, clip_start_time, movie_id FROM subjects WHERE subject_type='clip'",
                    conn,)

        # Get start time of the clips and ids of the original movies
        frames_df = pd.merge(frames_df, subjects_df, how="left", left_on="subject_id", right_on="id").drop(columns=["id"])
        
        # Identify the second of the original movie when the species first appears
        frames_df["first_seen_movie"] = (
            frames_df["clip_start_time"] + frames_df["first_seen"]
        )
       

        # Get the filepath and fps of the original movies
        f_paths = pd.read_sql_query(f"SELECT id, fpath, fps FROM movies", conn)
        f_paths["fpath"] = movie_folder + f_paths["fpath"]

        # Ensure swedish characters don't cause issues
        f_paths["fpath"] = f_paths["fpath"].apply(k_utils.unswedify)
        
        # Include movies' filepath and fps to the df
        frames_df = frames_df.merge(f_paths, left_on="movie_id", right_on="id")
        
        # Specify if original movies can be found
        # frames_df["fpath"] = frames_df["fpath"].apply(lambda x: x.encode('utf-8'))
        movie_paths = [k_utils.unswedify(str(Path(movie_folder, x))) for x in s_utils.get_snic_files(server_dict["client"], movie_folder).spath.values]
        frames_df["exists"] = frames_df["fpath"].apply(lambda x: True if x in movie_paths else False)
                                                      
        if len(frames_df[~frames_df.exists]) > 0:
            logging.error(
                f"There are {len(frames_df) - frames_df.exists.sum()} out of {len(frames_df)} frames with a missing movie"
            )

        # Select only frames from movies that can be found
        frames_df = frames_df[frames_df.exists]

        # Identify the ordinal number of the frames expected to be extracted
        frames_df["frame_number"] = frames_df[["first_seen_movie", "fps"]].apply(
            lambda x: [
                int((x["first_seen_movie"] + j) * x["fps"]) for j in range(n_frames)
            ], 1
        )

        # Reshape df to have each frame as rows
        lst_col = "frame_number"

        frames_df = pd.DataFrame(
            {
                col: np.repeat(frames_df[col].values, frames_df[lst_col].str.len())
                for col in frames_df.columns.difference([lst_col])
            }
        ).assign(**{lst_col: np.concatenate(frames_df[lst_col].values)})[
            frames_df.columns.tolist()
        ]

        # Drop unnecessary columns
        frames_df.drop(["subject_id"], inplace=True, axis=1)

    return frames_df

def upload_frames_to_zooniverse(upload_to_zoo, sitename, species_list, created_on, project):
    
    # Estimate the number of clips
    n_frames = upload_to_zoo.shape[0]
    
    # Create a new subject set to host the frames
    subject_set = SubjectSet()

    subject_set_name = str(int(n_frames)) + "_frames_" + "_".join(species_list) + "_" + sitename + "_" + created_on
    subject_set.links.project = project
    subject_set.display_name = subject_set_name

    subject_set.save()

    print(subject_set_name, "subject set created")

    # Save the df as the subject metadata
    subject_metadata = upload_to_zoo.set_index('frame_path').to_dict('index')

    # Upload the clips to Zooniverse (with metadata)
    new_subjects = []

    print("uploading subjects to Zooniverse")
    for frame_path, metadata in tqdm(subject_metadata.items(), total=len(subject_metadata)):
        subject = Subject()
        
        
        subject.links.project = project
        subject.add_location(frame_path)
        
        print(frame_path)
        subject.metadata.update(metadata)
        
        print(metadata)
        subject.save()
        print("subject saved")
        new_subjects.append(subject)

    # Upload videos
    subject_set.add(new_subjects)
    print("Subjects uploaded to Zooniverse")

# def choose_clip_workflows(workflows_df):

#     layout = widgets.Layout(width="auto", height="40px")  # set width and height

#     # Display the names of the workflows
#     workflow_name = widgets.SelectMultiple(
#         options=workflows_df.display_name.unique().tolist(),
#         description="Workflow name:",
#         disabled=False,
#     )

#     display(workflow_name)
#     return workflow_name

# def select_workflow(class_df, workflows_df, db_path):
#     # Connect to koster_db
#     conn = db_utils.create_connection(db_path)

#     # Query id and subject type from the subjects table
#     subjects_df = pd.read_sql_query("SELECT id, subject_type, https_location, clip_start_time, movie_id FROM subjects WHERE subject_type='clip'", conn)

#     # Add subject information based on subject_ids
#     class_df = pd.merge(
#         class_df,
#         subjects_df,
#         how="left",
#         left_on="subject_ids",
#         right_on="id",
#     )

#     # Select only classifications submitted to clip subjects
#     clips_class_df = class_df[class_df.subject_type=='clip']

#     # Save the ids of clip workflows
#     clip_workflow_ids = class_df.workflow_id.unique()

#     # Select clip workflows with classifications
#     clip_workflows_df = workflows_df[workflows_df.workflow_id.isin(clip_workflow_ids)]

#     # Select the workflows of the video classifications you want to aggregrate
#     workflow_names = choose_clip_workflows(clip_workflows_df)
    
#     return clips_class_df, workflow_names, workflows_df


# def select_workflow_version(w_names, workflows_df):
    
#     # Select the workflow ids based on workflow names
#     workflow_ids = workflows_df[workflows_df.display_name.isin(w_names)].workflow_id.unique()
    
#     # Create empty vector to save the versions selected
#     w_versions_list = []

#     for w_name in w_names:

#         # Estimate the versions of the workflow of interest
#         w_versions_available = workflows_df[workflows_df.display_name==w_name].version.unique()

#         # Display the versions of the workflow available
#         choose_clip_w_version = widgets.Dropdown(
#             options=list(map(float, w_versions_available)),
#             description= "Min version for " + w_name + ":",
#             disabled=False
#         )

#         # Display a button to select the version
#         btn = widgets.Button(description='Select')
#         display(choose_clip_w_version, btn)

#         def update_version_list(obj):
#             print('You have selected',choose_clip_w_version.value)
#             w_versions_list = w_versions_list.append(w_name+"_"+choose_clip_w_version.value)
#         btn.on_click(update_version_list)

#     return w_versions_list