from git import Repo
from base_class import Website, Commit, FileMetadata, Plugin, PluginFile
from multiprocessing import Pool, cpu_count, Array, Process, Manager, SimpleQueue
import os, git, magic, sys
import re, time
import copy
import subprocess
import json

from analysis_wp_plugin import Analysis_WP_Plugin
from analysis_jo_plugin import Analysis_Jo_Plugin
from analysis_dr_plugin import Analysis_Dr_Plugin

sys.path.insert(0, './analysis_passes') # Import from subdirectory
from analysis_obf_plugin import Analysis_Obf_Plugin
from analysis_cryptominer import Analysis_Cryptominer
from analysis_corona import Analysis_Corona
from analysis_blacklist import Analysis_Blacklist
from analysis_fake_blacklist import Analysis_Fake_Blacklist
from analysis_err_report import Analysis_Err_Report
from analysis_shell_detect import Analysis_Shell_Detect
from analysis_fc_plugin import Analysis_FC_Plugin
from analysis_spam_plugin import Analysis_Spam_Plugin
from analysis_bh_seo_plugin import Analysis_BlackhatSEO_Plugin
from analysis_api_abuse import Analysis_API_Abuse
from analysis_covid_plugin import Analysis_Covid_Plugin
from analysis_downloader_plugin import Analysis_Downloader_Plugin
from analysis_gated_plugin import Analysis_Gated_Plugin
from analysis_bot_seo import Analysis_Bot_SEO
from analysis_newdown_plugin import Analysis_NewDown_Plugin

OUTPUT_BUCKET = "cyfi-plugins-results"
plugin_analyses = {}
plugin_analyses["WordPress"] = [Analysis_WP_Plugin()]
plugin_analyses["Joomla"]   = [Analysis_Jo_Plugin(), Analysis_WP_Plugin()]
plugin_analyses["Drupal"]   = [Analysis_Dr_Plugin(), Analysis_WP_Plugin()]

# Malicious plugin detection analyses
mal_plugin_analyses = [
        Analysis_Obf_Plugin(),         # Obfuscation
        Analysis_Cryptominer(),        # Cryptomining
        Analysis_Blacklist(),          # Blacklisted Plugin Names and versions
        Analysis_Fake_Blacklist(),     # Blacklisted Fake Plugin Names
        Analysis_Err_Report(),         # Disable Error Reporting
        Analysis_Shell_Detect(),       # Webshells in plugins
        Analysis_FC_Plugin(),          # Function Construction
        Analysis_Spam_Plugin(),        # Spam Injection
        Analysis_BlackhatSEO_Plugin,
        Analysis_API_Abuse(),          # Abuse of WP API
        Analysis_Covid_Plugin(),       # COVID-19
        Analysis_Downloader_Plugin(),  # Downloaders
        Analysis_Gated_Plugin(),       # Gated Plugins
        Analysis_Bot_SEO(),            # SEO against Google bot
        Analysis_NewDown_Plugin(),     # Nulled Plugin
        Analysis_Corona()              # Coronavirus regex
        # Analysis_Out_Extract()         # Extract Outputs
]


class Framework():

    def __init__(self, website_path =None):
        if website_path.endswith("/"):
            pass
        else:
            website_path = website_path + "/"
        self.website = Website(website_path)    
        self.commits = []
        # Variables used to fix git mistakes in filenames that contain non-ascii characters
        self.octals = re.compile('((?:\\\\\d\d\d)+)')
        self.three_digits = re.compile('\d\d\d')
        print("CMS", self.website.cms)

    def GetCommitList(self, repo):
        ''' Get git commit objects and create a list of Commit objects for each
        commit 
        '''
        commit_list = list(repo.iter_commits('master'))
        commits = []
        for c in commit_list:
            commits.append(Commit(c))
        return commits

    def fix_git_trash_strings(self, git_trash):
        ''' Git diff.a_path and b_path replace non-ascii chacters by their
        octal values and replace it as characters in the string. This function 
        fixes thhis BS.
        '''
        git_trash = git_trash.lstrip('\"').rstrip('\"') 
        match = re.split(self.octals, git_trash)
        pretty_strings = []
        for words in match:
            if re.match(self.octals,words):
                ints = [int(x, 8) for x in re.findall(self.three_digits, words)]
                pretty_strings.append(bytes(ints).decode())
            else:
                pretty_strings.append(words)
        return ''.join(pretty_strings)

    def GetFileList(self, c_obj, init):
        exclude = ['.codeguard', '.git', '.gitattributes']
        file_list = []
        ma = magic.Magic(mime=True)

        # Parse through all the directories and get all files for the first commit or if the previous commit has zero files
        num_files = 0
        if c_obj == self.commits[0] or init:
            for fpath, dirs, files in os.walk(self.website.website_path, topdown=True):
                # Exclude files in .git and .codeguard directories
                dirs[:] = [d for d in dirs if d not in exclude]
                files[:] = [fs for fs in files if fs not in exclude]

                # If no files in this commit, then set c_obj.initial to False so we get full filelist again in the next commit
                if files:
                    c_obj.initial = True

                # For the first commit, the state is considered as file added(A)
                for f in files:
                    full_path = os.path.join(fpath, f)
                    if os.path.islink(full_path):
                        mime = 'sym_link'
                    else:
                        # mime = ma.from_file(full_path.encode(sys.getfilesystemencoding(), 'surrogateescape'))
                        try:
                            mime = ma.from_file(full_path.encode("utf-8", 'surrogateescape'))
                            # mime = ma.from_file(full_path)
                        except  Exception as e:
                            print("MIME_ERROR:", e, "Could no encode filename", full_path)
                            mime = None
                        file_list.append(FileMetadata(full_path, f, 'A', mime))
            num_files = len(file_list)
        else:
            '''Second commit onwards, copy the file_list from the previous commit, 
            and only modify changed files. Add new files if any, and change the state
            of modified or renamed files.
            '''
            prev_index = self.commits.index(c_obj) - 1
            file_list = copy.deepcopy(self.commits[prev_index]._file_list)

            # Free up memory
            self.commits[prev_index]._file_list = None

            found_index_list = []
            for diff in c_obj.parent.diff(c_obj.commit_obj):
                # Ignore all the changes in .codeguard directors
                if '.codeguard' not in diff.b_path:
                    '''Side note:
                    diff.a_path -> path of the file in parent (older) commit object
                    diff.b_path -> path of the file in child (newer)commit object
                    If a file is renamed, the old name is considered 'deleted' in the new commit
                    '''
                    # Clean up git python string madness for non-ascii characters
                    if re.search(self.octals, diff.a_path):
                        diff_a_path = self.fix_git_trash_strings(diff.a_path)
                    else:
                        diff_a_path = diff.a_path
                    if re.search(self.octals, diff.b_path):
                        diff_b_path = self.fix_git_trash_strings(diff.b_path)
                    else:
                        diff_b_path = diff.b_path

                    # Note for @Victor
                    # print("A_MODE", diff.a_mode, diff_a_path)
                    # print("B_MODE", diff.b_mode, diff_b_path)

                    # For renamed files, consider the orginal path as deleted
                    if diff.change_type == 'R':
                        search_path = self.website.website_path + '/' + diff_a_path
                        found_index = self.search_file_list(search_path, file_list)
                        if found_index != None:
                            file_list[found_index].state = 'D'


                    search_path = self.website.website_path + diff_b_path
                    found_index = self.search_file_list(search_path, file_list)
                    # print(found_index,diff_b_path, diff.change_type)
                    if (found_index != None):
                        file_list[found_index].state = diff.change_type
                        found_index_list.append(found_index)
                        # If there is permission change, update fileMetadata object
                        if diff.a_mode != 0 and diff.b_mode != 0:
                            if diff.a_mode != diff.b_mode:
                                file_list[found_index].permission_change = True
                        # print('FOUND', diff.change_type, diff.b_path)
                    else:
                        # Index not found implies a new file is being added
                        f_name_only = search_path.split('/')[-1]
                        try:
                            mime_type = ma.from_file(search_path.encode("utf-8", 'surrogateescape'))
                        except OSError as e:
                            mime_type = None
                        file_list.append(FileMetadata(search_path, f_name_only, diff.change_type, mime_type))
                        found_index_list.append(len(file_list) - 1)

            num_del_files = 0
            for indx, file_obj in enumerate(file_list):
                if file_obj.state in ['D', 'NC_D']:
                    num_del_files += 1
                if indx not in found_index_list:
                    if file_obj.state == 'D' or file_obj.state == 'NC_D':
                        file_obj.state = 'NC_D'  # Deleted in the previous commit and did not come back in this commit
                    else:
                        file_obj.state = 'NC'
            num_files = len(file_list) - num_del_files

        return file_list, num_files

    def search_file_list(self, search_item, file_list):
        #print(search_item)
        for f_item in file_list:
            if f_item.filepath == search_item:
                return file_list.index(f_item)
        return None


    def run(self):
        magic_obj = magic.Magic(mime=True)
        analysis_start = time.time()
        repo = Repo(self.website.website_path)
        p = Pool(cpu_count())

        if not repo.bare:
            # Get all commits
            self.commits = self.GetCommitList(repo)
            self.commits.reverse()  # Reversing to start with the oldest commit first

            # Initial commit -- use init and flag to assign cms if first commit has no files
            # Use init with getFileList if first commit has no files
            init = True
            li = []
            fil = []
            bin = []
            for c_obj in self.commits:
                try:
                    repo.git.checkout(c_obj.commit_id)
                except git.GitCommandError as e:
                    # If local change error, delete em and re run :)
                    if 'overwritten by checkout:' in str(e):
                        repo.git.reset('--hard')
                        repo.git.checkout(c_obj.commit_id)
                files, c_obj.num_files = copy.deepcopy(self.GetFileList(c_obj, init))
                if not files:
                    continue
                init = False
                for pf_obj in files:
                    if os.path.isfile(pf_obj.filepath) and os.path.exists(pf_obj.filepath):
                        mimetypes = magic_obj.from_file(pf_obj.filepath)
                        if 'application/octet-stream' in mimetypes or 'application/vnd,android.package-archive' in mimetypes:
                            bin.append(pf_obj.filepath)
                    if not pf_obj.filepath.endswith('php'):
                        continue
                    plu_obj = PluginFile
                    plu_obj.plugin_name = pf_obj.filename.split(".")[0]
                    plu_obj.filepath = pf_obj.filepath  # full filepath wrt website-xxx/<filepath>
                    plu_obj.state = pf_obj.state  # holds A | D | M (added, deleted, modified etc.)
                    plu_obj.mime_type = 'x.php'
                    plu_obj.suspicious_tags = pf_obj.suspicious_tags
                    plu_obj.is_malicious = None
                    plu_obj.extracted_results = {}  # Key suspicious tag. Nested dictionary of values
                    plu_obj.ast = None
                    plu_obj=DoMalFileDetect(plu_obj)
                    if(plu_obj.is_malicious):
                        fil.append(plu_obj.filepath)
                        li.append(plu_obj.filepath)
                        li.append(plu_obj.suspicious_tags)
                        li.append(plu_obj.extracted_results)
            op_path = "results/" + self.website.website_path.split('/')[-2] +'_0'+ ".txt"
            if not os.path.isdir('results'):  # mkdir results if not exists
                os.makedirs('results')
            with open(op_path, 'w') as f:
                json.dump(li, f, default=str, ensure_ascii=False) # the result
            op_path_2 = "results/" + self.website.website_path.split('/')[-2] +'_1'+ ".txt"
            with open(op_path_2, 'w') as f:
                json.dump(fil, f, default=str, ensure_ascii=False) # the php that malicious
            op_path_3 = "results/" + self.website.website_path.split('/')[-2] + '_2' + ".txt"
            with open(op_path_3, 'w') as f:
                json.dump(bin, f, default=str, ensure_ascii=False) # all the binaries
        else:
            print('Could not load repository at {} :('.format(self.website.website_path))

def DoMalFileDetect(plu_obj):
    if not os.path.isfile(plu_obj.filepath) or not os.path.exists(plu_obj.filepath):
        return plu_obj
    with open(plu_obj.filepath, 'r', errors="ignore") as f:
        read_data = f.read()
    try:    # Generate AST for Analysis Passes
        cmd = [
                  'php',
                  '-f',
                  './analysis_passes/generateAST.php',
                  plu_obj.filepath
              ]
        plu_obj.ast = subprocess.check_output(cmd)
    except Exception as e:
        print("ENCOUNTERED EXCEPTION {} FOR {}".format(e, plu_obj.filepath))
    for reanalysis in mal_plugin_analyses:
        reanalysis.reprocessFile(plu_obj, read_data)
    plu_obj.ast=None # mem cleanup
    if plu_obj.suspicious_tags:
        plu_obj.is_malicious = True
    return plu_obj


if __name__=="__main__":
    website_path = sys.argv[1]
    start = time.time()
    my_framework = Framework(website_path=website_path)
    my_framework.run()
    print("Time taken: ", time.time() - start)
