Semantic Change Description Evolution

Project Objective
    The objective of this project is to collect and analyze the semantic evolution of change descriptions in Gerrit-based projects, with a focus on ONAP, Wikimedia, and LibreOffice.
    The pipeline aims to progressively eliminate metadata-related and syntactic noise in order to retain only meaningful change-description evolutions.

Pipeline Overview
Gerrit Projects
        |
        v
Extraction and Cleaning Module
        |
        v
Verified Pairs Dataset
        |
        v
Edit Distance Module
        |
        v
Distance Dataset
        |
        v
Filtering Module
        |
        v
Final Dataset

Module 1: Extraction and Cleaning

    Input
    Gerrit project (ONAP, Wikimedia, LibreOffice)
    Output
    verified_pairs.csv

    Responsibilities

    Step 1: Extract Consecutive Patchset Pairs
        For each change:
            PS1
            PS2
            PS3
            PS4
            etc.. 
        Generate:
            PS1 -> PS2
            PS2 -> PS3
            PS3 -> PS4
        For each pair, store:
            change_id
            old_patchset
            new_patchset
            old_description
            new_description

    Step 2: Clean Descriptions
        Remove non-informative content including:
            Change-Id
            Signed-off-by
            Reviewed-by
            Tested-by
            Acked-by
            Depends-On
            Hosts
            Co-authored-by
            Reported-by
            Suggested-by
            Cc
            Fixes
            Related
            Bug
            Issue
            Closes
            See-also
            Reviewed-on
            cherry picked from commit
        Normalize:
            whitespace
            empty lines
            formatting artifacts
    Step 3: Verify Description Evolution
        Keep only pairs where:
            clean_old_description != clean_new_description
        Output file:
            verified_pairs.csv
        Columns:
        change_id
        old_patchset
        new_patchset
        old_description_clean
        new_description_clean


Module 2: Edit Distance Computation
    Input
    verified_pairs.csv
    Output
    pairs_with_distance.csv

    Responsibilities
        Compute the Levenshtein edit distance between:
            old_description_clean
            new_description_clean
        Store:
            change_id
            old_patchset
            new_patchset
            old_description_clean
            new_description_clean
            edit_distance

Module 3: Filtering
    Input
        pairs_with_distance.csv
    Output
        final_dataset.csv

    Responsibilities
    Apply project-specific edit-distance thresholds.
    Example:
        LibreOffice > 200
        Wikimedia > 150
        ONAP > 75
    Only pairs exceeding the corresponding threshold will be retained.

    Expected Datasets
        verified_pairs.csv
            Contains all patchset pairs exhibiting description evolution after cleaning.
        pairs_with_distance.csv
            Contains all verified pairs with computed edit-distance values.
        final_dataset.csv
            Contains the final set of candidate semantic-evolution instances.

Future Improvements
    Potential future extensions include:
        Semantic similarity filtering using embeddings.
        Patchset-count analysis.
        Automatic taxonomy classification.
        Prediction of future change-description evolution.