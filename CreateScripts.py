#This script generate the scripts to run all the instances on server grids.

#SBATCH --mail-user=simon.thevenin@imt-atlantique.fr
import sys
import csv

def CreatHeader(file, filename):
       CreatHeaderNantes(file, filename)

def CreatHeaderNantes(file, filename):
    file.write("""#!/bin/bash
# Nom du job
#SBATCH -J MON_JOB_MPI
#
# Partition visee
#SBATCH --partition=MPI-short
#
# Nombre de noeuds
#SBATCH --nodes=1
# Nombre de processus MPI par noeud
#SBATCH --ntasks-per-node=1
#SBATCH --mem 10000
#
# Temps de presence du job
#SBATCH --time=2:00:00
#
# Adresse mel de l'utilisateur
#
# Envoi des mails
#SBATCH --mail-type=abort,end
#
#SBATCH -o /home/LS2N/thevenin-s/log/job_mpi-%s.out
 
module purge
module load intel/2016.3.210
module load intel/mkl/64/2016.3.210
module load intel/mpi/2016.3.210
module load python/3.7.4
module load intel/mkl/64/2017.4.196
module load gcc/12.1.0

 
export LD_PRELOAD=/lib64/psm2-compat/libpsm_infinipath.so.1

#
# Faire le lien entre SLURM et Intel MPI
export I_MPI_PMI_LIBRARY=/usr/lib64/libpmi.so

"""%(filename))

#SBATCH --mail-user=simon.thevenin@imt-atlantique.fr

def CreateJob(nb_packages, distribution, version):
    qsub_filename = "./Jobs/job_%s_%s_%s" % (nb_packages, distribution, version)
    qsub_file = open(qsub_filename, 'w+')
    CreatHeader(qsub_file , qsub_filename)
    qsub_file.write("""
srun python main.py %s %s %s --full_patterns -t 3600 > /home/LS2N/thevenin-s/log/output-${SLURM_JOB_ID}.txt 
""" % (nb_packages, distribution, version))

    return qsub_filename

if __name__ == "__main__":

	# Packages = [ 50, 100, 200 ]# "EMP"]# "ZINB"]
# 
#     Distribution = ["-n", "-u", "-g"]
    
    Packages = [ 50 ]# "EMP"]# "ZINB"]

    Distribution = ["-u"]

    filenewname = "runtestOpt.sh"
    filenew = open(filenewname, 'w')
    filenew.write("""
    #!/bin/bash -l
    #
    """)
    
    for p in Packages:
    	for d in Distribution:
    		for v in range(1):
    			jobname = CreateJob(p, d, v)
    			filenew.write("sbatch %s \n" % (jobname))
				
