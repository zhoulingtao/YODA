def run(self):
    analysis_start = time.time()
    repo = Repo(self.website.website_path)
    # print('***************************************************')
    # print('***************************************************')
    # print('Current Website:', self.website.website_path)
    # print('***************************************************')
    # print('***************************************************')

    # Create worker pool so the workers are alive for all commits
    p = Pool(cpu_count())

    if not repo.bare:
        # Get all commits
        self.commits = self.GetCommitList(repo)
        self.commits.reverse()  # Reversing to start with the oldest commit first

        # Initial commit -- use init and flag to assign cms if first commit has no files
        # Use init with getFileList if first commit has no files
        init = True
        flag = True

        for c_obj in self.commits:
            try:
                repo.git.checkout(c_obj.commit_id)
            except git.GitCommandError as e:
                # If local change error, delete em and re run :)
                if 'overwritten by checkout:' in str(e):
                    repo.git.reset('--hard')
                    repo.git.checkout(c_obj.commit_id)
                    # print('---------------------------------------------------')
            # print('Current Commit ID:', c_obj.commit_id, repo.head.commit.authored_datetime)
            # print('---------------------------------------------------')

            # Get all Files
            files, c_obj.num_files = copy.deepcopy(self.GetFileList(c_obj, init))
            # print("Number of files:", c_obj.num_files)

            # No point processing anything if the commit has no files
            if not files:
                continue
            init = False

            # If first commit had no files, cms will be unassigned. Reassign CMS 
            if flag and self.website.cms == None:
                self.website.cms = "Durpal"
                self.website.cms_version = 0.4
                flag = False

                # processCommit
            for analysis in plugin_analyses[self.website.cms]:
                analysis.processCommit(c_obj)

            ''' Copy plugin info from the prev commit to this one for all 
            commits except the first commit. If first commit has no files, then wait
            until we find a commit that has files.
            '''
            prev_index = self.commits.index(c_obj) - 1
            if prev_index != -1:
                c_obj.plugins = copy.deepcopy(self.commits[prev_index].plugins)

                # Do file operations on all files in a commit in parallel
            FixedCMSFileOps = partial(DoFileOperations,
                                      cms=self.website.cms)  # DoFileOperations has only one argument f_obj (cms is fixed)
            file_outs = p.map(FixedCMSFileOps, files)

            # files will contain the list of updated file objects (f_obj)
            files = []
            plugins = {}

            ''' file_outs = oputput from DoFileOperations is a tuple of 
            f_obj, file_info for plugins
            file_outs = [outs0, outs1, ...]
            outs[0] = f_obj
            outs[1] = file_info 
            '''
            if file_outs:
                for indx, outs in enumerate(file_outs):
                    # outs[1] will be not None only for A/M/R plugins 
                    if outs[1]:
                        if outs[1].plugin_name in plugins:
                            i = 0
                            # Duplicate plugin_name --- create p_name_#number
                            new_plugin_name = outs[1].plugin_name + '_' + str(i)
                            # print("NEW", new_plugin_name, outs[0].filepath)
                            while new_plugin_name in plugins:
                                i += 1
                                new_plugin_name = outs[1].plugin_name + '_' + str(i)
                            # Update corresponding plugin name in f_obj and p_obj
                            outs[0].plugin_name = new_plugin_name
                            outs[1].plugin_name = new_plugin_name
                        else:
                            new_plugin_name = outs[1].plugin_name
                        if outs[1].cms != self.website.cms:
                            outs[1].fake_wp_plugin = True
                            # print("Mismatch CMS Plugin:", new_plugin_name, outs[1].cms)
                        plugins[new_plugin_name] = outs[1]

                        # TODO Add a output processing field for this

                        # Update the corresponding plugin name in pf_obj
                        plugins[new_plugin_name].files[outs[0].filepath].plugin_name = new_plugin_name
                    else:
                        # If plugin state = NC then update state
                        if outs[0].is_plugin:
                            c_obj.plugins[outs[0].plugin_name].files[outs[0].filepath].state = outs[0].state
                    files.append(outs[0])

                # print("Number of added/modified plugins", len(plugins))

                # New plugins from added or modified files
                new_plugins = plugins  # Could be this for deepcopy TODO

            # Code snoppet to test without parallel processing
            # for f_obj in files:
            #   DoFileOperations(f_obj, c_obj)

            # Update the list of fileMetadata to the Commit object
            c_index = self.commits.index(c_obj)
            self.commits[c_index]._file_list = copy.deepcopy(files)

            ''' Copy plugin info from the prev commit to this one for all 
            commits except the first commit. If first commit has no files, then wait
            until we find a commit that has files.
            '''
            if c_obj.initial == True:
                c_obj.plugins = new_plugins

            else:
                # Update modified plugin info in c_obj.plugins, or add thm to c_obj, if new_plugins are added
                for p_name in new_plugins:
                    # New plugin added
                    if p_name not in c_obj.plugins:
                        c_obj.plugins[p_name] = new_plugins[p_name]
                    else:
                        # Plugin modified
                        for pf_name in new_plugins[p_name].files:
                            if pf_name not in c_obj.plugins[p_name].files:
                                c_obj.plugins[p_name].files[pf_name] = new_plugins[p_name].files[pf_name]
                        c_obj.plugins[p_name].version = new_plugins[p_name].version
                        c_obj.plugins[p_name].author = new_plugins[p_name].author
                        c_obj.plugins[p_name].author_uri = new_plugins[p_name].author_uri
                        c_obj.plugins[p_name].author_email = new_plugins[p_name].author_email
                        c_obj.plugins[p_name].plugin_uri = new_plugins[p_name].plugin_uri
                        # c_obj.plugins[p_name].num_files= new_plugins[p_name].num_files
                        # c_obj.plugins[p_name].num_file_types= new_plugins[p_name].num_file_types

            # All plugins get populated here 
            # postProcessCommit
            # Since all plugins have the same postprocesscommit, we don't want to repeat it each time
            analysis = plugin_analyses[self.website.cms][0]
            # for analysis in plugin_analyses[self.website.cms]:
            analysis.postProcessCommit(c_obj)

            # By now, all plugin f_objs are tagged correctly with is_plugin
            # set to true. Now we count number of files and file types in 
            # each plugin and assign an effective state for the full plugin
            del_plugins = 0
            for p_name in c_obj.plugins:
                p_obj = c_obj.plugins[p_name]
                # Debug Print
                add, mod, dlt, nc, nc_d = self.CountPluginFiles(c_obj, p_obj)

                # Derive and assign plugin state 
                if add and not (mod or dlt or nc or nc_d):
                    if c_obj.plugins[p_name].plugin_state in ['A', 'NC', 'M']:
                        c_obj.plugins[p_name].plugin_state = 'M'
                    else:
                        c_obj.plugins[p_name].plugin_state = 'A'
                elif dlt and not (add or mod or nc or nc_d):
                    if p_obj.num_files > 0:
                        c_obj.plugins[p_name].plugin_state = 'M'
                    else:
                        c_obj.plugins[p_name].plugin_state = 'D'
                        del_plugins += 1
                elif nc and not (add or dlt or mod or nc_d):
                    c_obj.plugins[p_name].plugin_state = 'NC'
                elif nc_d and not (add or dlt or mod or nc):
                    c_obj.plugins[p_name].plugin_state = 'NC_D'
                    del_plugins += 1
                else:
                    c_obj.plugins[p_name].plugin_state = 'M'

                # print("PLG_STATE", c_obj.plugins[p_name].plugin_state, c_obj.commit_id, c_obj.date, add, mod, dlt, nc, nc_d, p_name)
            c_obj.num_active_plugins = len(c_obj.plugins) - del_plugins
            # print("Number of active plugins:", c_obj.num_active_plugins)
            # NC to plugins till here

            to_analyze_plugins = []
            p_state_nc = True
            for p_name in c_obj.plugins:
                p_obj = c_obj.plugins[p_name]
                if p_state_nc and p_obj.plugin_state in ['A', 'M', 'R', 'D']:
                    p_state_nc = False
                # if p_obj.is_theme:
                # print("************************************************")
                # print("FINAL PLUGIN", p_name)
                # print("theme_name", p_obj.theme_name)
                # print("Base path:", p_obj.plugin_base_path)
                # print("Version", p_obj.version)
                # print("Author", p_obj.author)
                # print("Author URI", p_obj.author_uri)
                # print("Plugin URI", p_obj.plugin_uri)
                # print("Plugin Score", p_obj.plugin_score)
                # if p_obj.fake_wp_plugin:
                #    print("Fake WordPress Plugin", p_obj.fake_wp_plugin)
                ## Debug Print
                # print("NUM OF FILES",p_obj.num_files)
                # print("TYPES",p_obj.num_file_types)
                # print("STATE", p_obj.plugin_state)
                # print("************************************************")
                for pf_name in p_obj.files:
                    pf_obj = p_obj.files[pf_name]
                    if (pf_obj.state in ['A', 'M', 'R']) and ('php' in pf_obj.mime_type):
                        to_analyze_plugins.append(pf_obj)

            # If there are A/M/D/R plugins, then c_obj.plugins_changed = True. If all plugins are NC/NC_D, then c_obj.plugins_changed = False
            c_obj.plugins_changed = not (p_state_nc)
            print("Plugins changed:", c_obj.plugins_changed)

            p_outs = p.map(DoMalFileDetect, to_analyze_plugins)

            if os.path.exists('./tmp'):  # rm FC pass's tempfile
                os.remove('./tmp')
            if os.path.exists('./urls'):  # rm SEO pass's tempfile
                os.remove('./urls')

            # Update the plugin info on the commit object
            for pf_obj in p_outs:
                c_obj.plugins[pf_obj.plugin_name].files[pf_obj.filepath] = pf_obj
            tot_mal_pfiles = 0
            num_mal_plugins = 0
            mal_pnames = []
            for p_name in c_obj.plugins:
                p_obj = c_obj.plugins[p_name]
                dir_path = Path(p_obj.plugin_base_path)
                plg_size = sum(f.stat().st_size for f in dir_path.glob('**/*') if f.is_file())
                c_obj.plugins[p_name].size = plg_size
                num_mal_p_files = 0
                for pf_name in p_obj.files:
                    pf_obj = p_obj.files[pf_name]
                    if (pf_obj.state in ['A', 'M', 'R', 'NC']) and ('php' in pf_obj.mime_type):
                        if pf_obj.suspicious_tags:
                            num_mal_p_files += 1
                            # print("M_PFILE", pf_obj.state, pf_obj.filepath, pf_obj.suspicious_tags, pf_obj.plugin_name)
                    if (pf_obj.state in ['D']):
                        pf_obj.suspicious_tags = []
                tot_mal_pfiles += num_mal_p_files

                if num_mal_p_files:
                    # print("Number of mal p_files", num_mal_p_files, "in plugin", p_obj.plugin_name, "size", plg_size)
                    c_obj.plugins[p_name].num_mal_p_files = num_mal_p_files

                if num_mal_p_files or p_obj.fake_wp_plugin:
                    c_obj.plugins[p_name].is_mal = True
                    num_mal_plugins += 1
                    mal_pnames.append(p_name)
                else:
                    c_obj.plugins[p_name].is_mal = False
            # print("Number of mal plugins", num_mal_plugins, c_obj.commit_id)
            # print("Total number of mal files", tot_mal_pfiles, c_obj.commit_id)
            if num_mal_plugins:
                c_obj.has_mal_plugins = True
                c_obj.num_mal_plugins = num_mal_plugins
                c_obj.tot_mal_pfiles = tot_mal_pfiles
                c_obj.mal_pnames = copy.deepcopy(mal_pnames)
            else:
                c_obj.has_mal_plugins = False

            # break #This breaks after first commit. Use for dbg purposex

        # postProcessWebsite
        for analysis in mal_plugin_analyses:
            analysis.postProcessWebsite(self.commits, self.website)

        website_output = self.process_outputs(self.website, self.commits, "Valid CMS", analysis_start)
        if 'ENVIRONMENT' in os.environ:
            pass
        else:
            op_path = "results/" + self.website.website_path.split('/')[-2] + ".json.gz"
            if not os.path.isdir('results'):  # mkdir results if not exists
                os.makedirs('results')

            with gzip.open(op_path, 'w') as f:
                f.write(json.dumps(website_output, default=str).encode('utf-8'))

    else:
        print('Could not load repository at {} :('.format(self.website.website_path))

    p.close()
    p.join()

