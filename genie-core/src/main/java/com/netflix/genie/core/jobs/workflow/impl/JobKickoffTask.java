/*
 *
 *  Copyright 2015 Netflix, Inc.
 *
 *     Licensed under the Apache License, Version 2.0 (the "License");
 *     you may not use this file except in compliance with the License.
 *     You may obtain a copy of the License at
 *
 *         http://www.apache.org/licenses/LICENSE-2.0
 *
 *     Unless required by applicable law or agreed to in writing, software
 *     distributed under the License is distributed on an "AS IS" BASIS,
 *     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *     See the License for the specific language governing permissions and
 *     limitations under the License.
 *
 */
package com.netflix.genie.core.jobs.workflow.impl;

import com.netflix.genie.common.dto.JobExecution;
import com.netflix.genie.common.dto.JobRequest;
import com.netflix.genie.common.exceptions.GenieException;
import com.netflix.genie.common.exceptions.GenieServerException;
import com.netflix.genie.core.jobs.JobConstants;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.exec.CommandLine;
import org.apache.commons.exec.Executor;
import org.apache.commons.lang3.StringUtils;
import org.apache.commons.lang3.SystemUtils;

import javax.validation.constraints.NotNull;
import java.io.File;
import java.io.IOException;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/**
 * Implementation of the workflow task for processing job information for genie mode.
 *
 * @author amsharma
 * @since 3.0.0
 */
@Slf4j
public class JobKickoffTask extends GenieBaseTask {

    private static final TimeZone UTC = TimeZone.getTimeZone("UTC");

    private boolean isRunAsUserEnabled;
    private boolean isUserCreationEnabled;
    private Executor executor;
    private String hostname;

    /**
     * Constructor.
     *
     * @param runAsUserEnabled Flag that tells if job should be run as user specified in the request
     * @param userCreationEnabled Flag that tells if the user specified should be created
     * @param executor An executor object used to run jobs
     * @param hostname Hostname for the node the job is running on
     */
    public JobKickoffTask(
        final boolean runAsUserEnabled,
        final boolean userCreationEnabled,
        final Executor executor,
        final String hostname
    ) {
        this.isRunAsUserEnabled = runAsUserEnabled;
        this.isUserCreationEnabled = userCreationEnabled;
        this.executor = executor;
        this.hostname = hostname;
    }
    /**
     * {@inheritDoc}
     */
    @Override
    public void executeTask(
        @NotNull
        final Map<String, Object> context
    ) throws GenieException, IOException {
        log.info("Executing Job Kickoff Task in the workflow.");
        super.executeTask(context);

        // At this point all contents are written to the run script and we call an explicit flush and close to write
        // the contents to the file before we execute it.
        try {
            writer.flush();
            writer.close();
        } catch (IOException e) {
            throw new GenieServerException("Failed to execute job with exception." + e);
        }

        final String runScript = this.jobWorkingDirectory
            + JobConstants.FILE_PATH_DELIMITER
            + JobConstants.GENIE_JOB_LAUNCHER_SCRIPT;

        if (this.isUserCreationEnabled) {
            createUser(this.jobExecEnv.getJobRequest().getUser(), this.jobExecEnv.getJobRequest().getGroup());
        }

        final List<String> command = new ArrayList<>();
        if (this.isRunAsUserEnabled) {
            changeOwnershipOfDirectory(this.jobWorkingDirectory, this.jobExecEnv.getJobRequest().getUser());

            // This is needed because the genie.log file is still generated as the user running Genie system.
            makeDirGroupWritable(this.jobWorkingDirectory + "/genie/logs");
            command.add("sudo");
            command.add("-u");
            command.add(this.jobExecEnv.getJobRequest().getUser());
        }

        // Set the execute bit on the runs script. This allows the run script
        // to be interpreted based on the hashbang.
        setExecuteOnFile(runScript);

        // If the OS is linux use setsid to launch the process so that the entire process tree
        // is launched in process group id which is the same as the pid of the parent process
        if (SystemUtils.IS_OS_LINUX) {
            command.add("setsid");
        }
        command.add(runScript);

        // Cannot convert to executor because it does not provide an api to get process id.
        final ProcessBuilder pb = new ProcessBuilder(command);
        pb.directory(this.jobExecEnv.getJobWorkingDir());
        pb.redirectOutput(new File(this.jobExecEnv.getJobWorkingDir() + JobConstants.GENIE_LOG_PATH));
        pb.redirectError(new File(this.jobExecEnv.getJobWorkingDir() + JobConstants.GENIE_LOG_PATH));

        try {
            final Process process = pb.start();
            final int processId = this.getProcessId(process);
            final JobRequest request = this.jobExecEnv.getJobRequest();
            final Calendar calendar = Calendar.getInstance(UTC);
//            context.put(JobConstants.JOB_STARTED_KEY, new Date(calendar.getTime().getTime()));
            calendar.add(Calendar.SECOND, request.getTimeout());
            final JobExecution jobExecution = new JobExecution
                .Builder(this.hostname, processId, this.jobExecEnv.getCommand().getCheckDelay(), calendar.getTime())
                .withId(request.getId())
                .build();
            context.put(JobConstants.JOB_EXECUTION_DTO_KEY, jobExecution);
        } catch (IOException ie) {
            throw new GenieServerException("Unable to start command " + String.valueOf(command), ie);
        }
    }

    // Helper method to add write permissions to a directory for the group owner
    private void makeDirGroupWritable(final String dir) throws GenieServerException {
        log.debug("Adding write permissions for the directory " + dir + " for the group.");
        final CommandLine commandLIne  = new CommandLine("sudo");
        commandLIne.addArgument("chmod");
        commandLIne.addArgument("g+w");
        commandLIne.addArgument(dir);

        try {
            this.executor.execute(commandLIne);
        } catch (IOException ioe) {
            throw new GenieServerException("Could not make the job working logs directory group writable.");
        }
    }

    /**
     * Create user on the system.
     *
     * @param user user id
     * @param group group id
     * @throws GenieException If there is any problem.
     */
    public void createUser(
        final String user,
        final String group) throws GenieException {

        // First check if user already exists
        final CommandLine idCheckCommandLine = new CommandLine("id");
        idCheckCommandLine.addArgument("-u");
        idCheckCommandLine.addArgument(user);

        try {
            this.executor.execute(idCheckCommandLine);
            log.debug("User already exists");
        } catch (IOException ioe) {
            log.debug("User does not exist. Creating it now.");

            // Create the group for the user.
            final CommandLine groupCreateCommandLine = new CommandLine("sudo");
            groupCreateCommandLine.addArgument("groupadd");
            groupCreateCommandLine.addArgument(group);

            // We create the group and ignore the error as it will fail if group already exists.
            // If the failure is due to some other reason, then user creation will fail and we catch that.
            try {
                this.executor.execute(groupCreateCommandLine);
            } catch (IOException ioexception) {
                log.debug("Group creation  threw an error as it might already exist");
            }

            final CommandLine userCreateCommandLine = new CommandLine("sudo");
            userCreateCommandLine.addArgument("useradd");
            userCreateCommandLine.addArgument(user);

            if (StringUtils.isNotBlank(group)) {
                userCreateCommandLine.addArgument("-G");
                userCreateCommandLine.addArgument(group);
            }

            userCreateCommandLine.addArgument("-M");

            try {
                this.executor.execute(userCreateCommandLine);
            } catch (IOException ioexception) {
                throw new GenieServerException("Could not create user " + user + "with exception " + ioexception);
            }
        }
    }

    /**
     * Method to change the ownership of a directory.
     *
     * @param dir The directory to change the ownership of.
     * @param user Userid of the user.
     * @throws GenieException If there is a problem.
     */
    public void changeOwnershipOfDirectory(
        final String dir,
        final String user) throws GenieException {

        final CommandLine commandLine = new CommandLine("sudo");
        commandLine.addArgument("chown");
        commandLine.addArgument("-R");
        commandLine.addArgument(user);
        commandLine.addArgument(dir);

        try {
            this.executor.execute(commandLine);
        } catch (IOException ioexception) {
            throw new GenieServerException("Could not change ownership with exception " + ioexception);
        }
    }

    /**
     * Helper method  to get process id for the given process.
     *
     * @param proc java process object representing the job launcher
     * @return pid for this process
     * @throws GenieException if there is an error getting the process id
     */
    private int getProcessId(final Process proc) throws GenieException {
        log.debug("called");

        try {
            final Field f = proc.getClass().getDeclaredField(JobConstants.PID);
            f.setAccessible(true);
            return f.getInt(proc);
        } catch (final IllegalAccessException
            | IllegalArgumentException
            | NoSuchFieldException
            | SecurityException e) {
            final String msg = "Can't get process id for job";
            log.error(msg, e);
            throw new GenieServerException(msg, e);
        }
    }

    /**
     * Method to set execute bit on a file.
     *
     * @param file Full path of file to set execute bit.
     * @throws GenieException If there is a problem.
     */
    public void setExecuteOnFile(final String file) throws GenieException {

        final CommandLine commandLine = new CommandLine("sudo");
        commandLine.addArgument("chmod");
        commandLine.addArgument("+x");
        commandLine.addArgument(file);

        try {
            this.executor.execute(commandLine);
        } catch (IOException ioexception) {
            throw new GenieServerException("Could not set execute with exception " + ioexception);
        }
    }

}
